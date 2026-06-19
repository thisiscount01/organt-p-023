'use strict';

const express = require('express');
const { WebSocketServer } = require('ws');
const http = require('http');
const path = require('path');
const { v4: uuidv4 } = require('uuid');

const PORT = process.env.PORT || 3000;
const TICK_RATE = 20;          // ticks per second
const TICK_MS = 1000 / TICK_RATE;
const MAP_W = 1200;
const MAP_H = 800;
const GAME_DURATION_MS = 3 * 60 * 1000;  // 3 minutes
const KILL_LIMIT = 10;
const RESPAWN_DELAY_MS = 8000;
const COUNTDOWN_DURATION_MS = 5000;
const END_RESET_DELAY_MS = 10000;

// ─── Hero definitions ────────────────────────────────────────────────────────
const HERO_DEFS = {
  tank: {
    maxHp: 400,
    speed: 180,
    basic: { cooldown: 500, damage: 80, radius: 60, type: 'melee' },
    skill1: { cooldown: 8000, damageMult: 0.5, duration: 3000, name: 'shield' },
    skill2: { cooldown: 10000, distance: 250, damage: 100, name: 'dash' },
    ult:   { cost: 100, radius: 200, damage: 100, stunDuration: 1000, name: 'quake' },
  },
  dps: {
    maxHp: 200,
    speed: 240,
    basic: { cooldown: 300, damage: 60, speed: 500, type: 'projectile' },
    skill1: { cooldown: 5000, spreadCount: 5, damage: 40, name: 'scatter' },
    skill2: { cooldown: 8000, distance: 200, name: 'blink' },
    ult:   { cost: 100, damage: 150, name: 'snipe' },
  },
  healer: {
    maxHp: 250,
    speed: 210,
    basic: { cooldown: 100, healPerSec: 30, damagePerSec: 30, range: 200, type: 'beam' },
    skill1: { cooldown: 5000, heal: 80, speed: 400, name: 'healorb' },
    skill2: { cooldown: 12000, duration: 2000, name: 'invincible' },
    ult:   { cost: 100, healPercent: 0.5, name: 'revive' },
  },
};

// ─── Map walls ───────────────────────────────────────────────────────────────
const WALLS = [
  // Center structure
  { x: 540, y: 340, w: 120, h: 120 },
  // Left cover pairs
  { x: 180, y: 200, w: 80, h: 20 },
  { x: 180, y: 580, w: 80, h: 20 },
  // Right cover pairs
  { x: 940, y: 200, w: 80, h: 20 },
  { x: 940, y: 580, w: 80, h: 20 },
  // Mid-left flank
  { x: 340, y: 370, w: 20, h: 60 },
  // Mid-right flank
  { x: 840, y: 370, w: 20, h: 60 },
  // Top-center
  { x: 530, y: 120, w: 140, h: 20 },
  // Bottom-center
  { x: 530, y: 660, w: 140, h: 20 },
];

// ─── Spawn points ─────────────────────────────────────────────────────────────
const SPAWNS = [
  [
    { x: 100, y: 300 },
    { x: 100, y: 400 },
    { x: 100, y: 500 },
  ],
  [
    { x: 1100, y: 300 },
    { x: 1100, y: 400 },
    { x: 1100, y: 500 },
  ],
];

// ─── Game state ───────────────────────────────────────────────────────────────
let game = createGame();

function createGame() {
  return {
    phase: 'waiting',        // waiting | countdown | playing | ended
    tick: 0,
    startTime: null,
    countdownStart: null,
    endTime: null,
    winner: null,
    scores: { 0: 0, 1: 0 },
    players: {},             // playerId -> player object
    projectiles: {},         // projId -> projectile object
    events: [],              // events to flush per tick
    teamSlots: [0, 1, 2, 3, 4, 5], // available slot indices
    nextProjectileId: 1,
  };
}

// ─── Player factory ──────────────────────────────────────────────────────────
function createPlayer(id, heroClass, team, ws) {
  const def = HERO_DEFS[heroClass];
  const slotIndex = game.teamSlots.findIndex((_, i) => {
    const taken = Object.values(game.players).filter(p => p.team === team);
    return taken.length < 3;
  });
  const teamPlayers = Object.values(game.players).filter(p => p.team === team).length;
  const spawn = SPAWNS[team][Math.min(teamPlayers, 2)];

  return {
    id,
    ws,
    heroClass,
    team,
    position: { x: spawn.x, y: spawn.y },
    angle: team === 0 ? 0 : Math.PI,
    hp: def.maxHp,
    maxHp: def.maxHp,
    status: 'alive',         // alive | dead
    respawnAt: 0,
    ultCharge: 0,
    shield: false,           // tank skill1 active
    shieldExpiry: 0,
    invincible: false,       // healer skill2 active
    invincibleExpiry: 0,
    stunned: false,
    stunnedUntil: 0,
    skills: {
      basicCd: 0,
      skill1Cd: 0,
      skill2Cd: 0,
    },
    lastSeq: 0,
  };
}

// ─── Geometry helpers ────────────────────────────────────────────────────────
function dist(a, b) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function circleRect(cx, cy, r, rx, ry, rw, rh) {
  const nearX = Math.max(rx, Math.min(cx, rx + rw));
  const nearY = Math.max(ry, Math.min(cy, ry + rh));
  const dx = cx - nearX;
  const dy = cy - nearY;
  return dx * dx + dy * dy < r * r;
}

function segmentIntersectsRect(x1, y1, x2, y2, rx, ry, rw, rh) {
  // Liang–Barsky algorithm
  const dx = x2 - x1;
  const dy = y2 - y1;
  let tMin = 0;
  let tMax = 1;

  const checks = [
    [-dx, x1 - rx],
    [dx, rx + rw - x1],
    [-dy, y1 - ry],
    [dy, ry + rh - y1],
  ];

  for (const [p, q] of checks) {
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const t = q / p;
      if (p < 0) tMin = Math.max(tMin, t);
      else tMax = Math.min(tMax, t);
      if (tMin > tMax) return false;
    }
  }
  return true;
}

function wallBlocksLine(x1, y1, x2, y2) {
  for (const w of WALLS) {
    if (segmentIntersectsRect(x1, y1, x2, y2, w.x, w.y, w.w, w.h)) return true;
  }
  return false;
}

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function resolveWallCollision(pos, radius) {
  // Clamp to map bounds first
  pos.x = clamp(pos.x, radius, MAP_W - radius);
  pos.y = clamp(pos.y, radius, MAP_H - radius);

  // Push out of each wall
  for (const w of WALLS) {
    if (circleRect(pos.x, pos.y, radius, w.x, w.y, w.w, w.h)) {
      // Find closest edge and push
      const cx = w.x + w.w / 2;
      const cy = w.y + w.h / 2;
      const overlapX = (w.w / 2 + radius) - Math.abs(pos.x - cx);
      const overlapY = (w.h / 2 + radius) - Math.abs(pos.y - cy);
      if (overlapX < overlapY) {
        pos.x += pos.x < cx ? -overlapX : overlapX;
      } else {
        pos.y += pos.y < cy ? -overlapY : overlapY;
      }
    }
  }
}

// ─── Utility ─────────────────────────────────────────────────────────────────
function now() { return Date.now(); }

function broadcast(msg) {
  const str = JSON.stringify(msg);
  for (const p of Object.values(game.players)) {
    if (p.ws && p.ws.readyState === 1) {
      p.ws.send(str);
    }
  }
}

function send(ws, msg) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
}

// ─── Ult charge helper ────────────────────────────────────────────────────────
function addUlt(player, amount) {
  player.ultCharge = Math.min(100, player.ultCharge + amount);
}

// ─── Apply damage ─────────────────────────────────────────────────────────────
function applyDamage(attacker, target, dmg, skillType) {
  if (!target || target.status !== 'alive') return;
  if (target.invincible) return;

  // Halve if target has shield active
  const effective = target.shield ? Math.floor(dmg * 0.5) : dmg;
  target.hp = Math.max(0, target.hp - effective);

  // Ult charge for both
  if (attacker) addUlt(attacker, 5);
  addUlt(target, 5);

  game.events.push({ type: 'hit', attackerId: attacker ? attacker.id : null, targetId: target.id, dmg: effective, skillType });

  if (target.hp <= 0) {
    killPlayer(attacker, target);
  }
}

function applyHeal(target, amount) {
  if (!target || target.status !== 'alive') return;
  target.hp = Math.min(target.maxHp, target.hp + amount);
}

function killPlayer(killer, victim) {
  victim.status = 'dead';
  victim.hp = 0;
  victim.respawnAt = now() + RESPAWN_DELAY_MS;

  if (killer && killer.team !== victim.team) {
    game.scores[killer.team] = (game.scores[killer.team] || 0) + 1;
    game.events.push({ type: 'kill', killerId: killer.id, victimId: victim.id });
    if (game.scores[killer.team] >= KILL_LIMIT) {
      endGame(killer.team);
    }
  }
}

function respawnPlayer(player) {
  const def = HERO_DEFS[player.heroClass];
  const teamPlayers = Object.values(game.players).filter(p => p.team === player.team && p.id !== player.id);
  const idx = Math.min(teamPlayers.length, 2);
  const spawn = SPAWNS[player.team][idx];

  player.status = 'alive';
  player.hp = def.maxHp;
  player.position = { x: spawn.x, y: spawn.y };
  player.stunned = false;
  player.stunnedUntil = 0;
  player.shield = false;
  player.shieldExpiry = 0;
  player.invincible = false;
  player.invincibleExpiry = 0;
}

// ─── Projectile factory ───────────────────────────────────────────────────────
function spawnProjectile(ownerId, type, x, y, angle, speed, damage, pierce, data) {
  const id = 'proj_' + (game.nextProjectileId++);
  game.projectiles[id] = {
    id,
    ownerId,
    type,
    position: { x, y },
    angle,
    speed,
    damage,
    pierce: pierce || false,
    data: data || {},
    hit: new Set(),
  };
  return id;
}

// ─── Game phase transitions ───────────────────────────────────────────────────
function tryStartCountdown() {
  if (game.phase !== 'waiting') return;
  const count = Object.keys(game.players).length;
  if (count < 1) return;

  game.phase = 'countdown';
  game.countdownStart = now();
  broadcast({ type: 'gameState', phase: 'countdown', countdown: 5 });
  console.log('[Game] Countdown started');
}

function startPlaying() {
  game.phase = 'playing';
  game.startTime = now();
  game.scores = { 0: 0, 1: 0 };
  // Reset all players
  for (const p of Object.values(game.players)) {
    const def = HERO_DEFS[p.heroClass];
    const teamPlayers = Object.values(game.players).filter(q => q.team === p.team && q.id < p.id).length;
    const spawn = SPAWNS[p.team][Math.min(teamPlayers, 2)];
    p.position = { x: spawn.x, y: spawn.y };
    p.hp = def.maxHp;
    p.maxHp = def.maxHp;
    p.status = 'alive';
    p.respawnAt = 0;
    p.ultCharge = 0;
    p.shield = false;
    p.shieldExpiry = 0;
    p.invincible = false;
    p.invincibleExpiry = 0;
    p.stunned = false;
    p.stunnedUntil = 0;
    p.skills = { basicCd: 0, skill1Cd: 0, skill2Cd: 0 };
  }
  game.projectiles = {};
  broadcast({ type: 'gameState', phase: 'playing' });
  console.log('[Game] Playing started');
}

function endGame(winnerTeam) {
  if (game.phase === 'ended') return;
  game.phase = 'ended';
  game.winner = winnerTeam;
  broadcast({ type: 'gameState', phase: 'ended', winner: winnerTeam, scores: game.scores });
  console.log('[Game] Ended — winner team', winnerTeam);

  setTimeout(() => {
    resetGame();
  }, END_RESET_DELAY_MS);
}

function resetGame() {
  const existingPlayers = Object.values(game.players).map(p => ({
    id: p.id,
    ws: p.ws,
    heroClass: p.heroClass,
    team: p.team,
  }));

  game = createGame();

  for (const ep of existingPlayers) {
    if (ep.ws && ep.ws.readyState === 1) {
      const def = HERO_DEFS[ep.heroClass];
      const teamPlayers = Object.values(game.players).filter(q => q.team === ep.team).length;
      const spawn = SPAWNS[ep.team][Math.min(teamPlayers, 2)];
      const player = createPlayer(ep.id, ep.heroClass, ep.team, ep.ws);
      game.players[ep.id] = player;
      send(ep.ws, { type: 'init', playerId: ep.id, team: ep.team, walls: WALLS });
    }
  }

  broadcast({ type: 'gameState', phase: 'waiting' });
  console.log('[Game] Reset');
}

// ─── Action handlers ──────────────────────────────────────────────────────────
function handleBasic(player, t) {
  const def = HERO_DEFS[player.heroClass];
  if (t < player.skills.basicCd) return;
  player.skills.basicCd = t + def.basic.cooldown;

  if (def.basic.type === 'melee') {
    // Tank AOE
    for (const target of Object.values(game.players)) {
      if (target.id === player.id || target.team === player.team || target.status !== 'alive') continue;
      if (dist(player.position, target.position) <= def.basic.radius) {
        applyDamage(player, target, def.basic.damage, 'basic');
      }
    }
  } else if (def.basic.type === 'projectile') {
    // DPS bullet
    spawnProjectile(player.id, 'bullet', player.position.x, player.position.y, player.angle, def.basic.speed, def.basic.damage, false);
  }
  // Healer beam is handled per tick (continuous), not on action
}

function handleSkill1(player, t) {
  const def = HERO_DEFS[player.heroClass];
  if (t < player.skills.skill1Cd) return;
  player.skills.skill1Cd = t + def.skill1.cooldown;

  if (player.heroClass === 'tank') {
    // Shield
    player.shield = true;
    player.shieldExpiry = t + def.skill1.duration;
  } else if (player.heroClass === 'dps') {
    // Scatter shot
    const baseAngle = player.angle;
    const angles = [-0.4, -0.2, 0, 0.2, 0.4];
    for (const offset of angles) {
      spawnProjectile(player.id, 'scatter', player.position.x, player.position.y, baseAngle + offset, 420, def.skill1.damage, false);
    }
  } else if (player.heroClass === 'healer') {
    // Heal orb projectile
    spawnProjectile(player.id, 'healorb', player.position.x, player.position.y, player.angle, def.skill1.speed, 0, false, { heal: def.skill1.heal });
  }
}

function handleSkill2(player, t) {
  const def = HERO_DEFS[player.heroClass];
  if (t < player.skills.skill2Cd) return;
  player.skills.skill2Cd = t + def.skill2.cooldown;

  if (player.heroClass === 'tank') {
    // Dash — move forward, damage on hit
    const dx = Math.cos(player.angle);
    const dy = Math.sin(player.angle);
    const steps = 10;
    for (let s = 1; s <= steps; s++) {
      const nx = player.position.x + dx * (def.skill2.distance / steps) * s;
      const ny = player.position.y + dy * (def.skill2.distance / steps) * s;
      const testPos = { x: nx, y: ny };
      resolveWallCollision(testPos, 20);
      if (Math.abs(testPos.x - nx) > 1 || Math.abs(testPos.y - ny) > 1) break;
      player.position.x = nx;
      player.position.y = ny;
    }
    resolveWallCollision(player.position, 20);
    // Damage enemies near final position
    for (const target of Object.values(game.players)) {
      if (target.id === player.id || target.team === player.team || target.status !== 'alive') continue;
      if (dist(player.position, target.position) <= 40) {
        applyDamage(player, target, def.skill2.damage, 'skill2');
      }
    }
  } else if (player.heroClass === 'dps') {
    // Blink teleport
    const dx = Math.cos(player.angle);
    const dy = Math.sin(player.angle);
    player.position.x += dx * def.skill2.distance;
    player.position.y += dy * def.skill2.distance;
    resolveWallCollision(player.position, 16);
  } else if (player.heroClass === 'healer') {
    // Invincible shield on nearest ally
    let nearest = null;
    let nearestDist = Infinity;
    for (const target of Object.values(game.players)) {
      if (target.id === player.id || target.team !== player.team || target.status !== 'alive') continue;
      const d = dist(player.position, target.position);
      if (d < nearestDist) { nearestDist = d; nearest = target; }
    }
    const target2 = nearest || player;
    target2.invincible = true;
    target2.invincibleExpiry = t + def.skill2.duration;
    game.events.push({ type: 'invincible', targetId: target2.id });
  }
}

function handleUlt(player, t) {
  if (player.ultCharge < 100) return;
  player.ultCharge = 0;
  const def = HERO_DEFS[player.heroClass];

  if (player.heroClass === 'tank') {
    // Quake — AOE
    for (const target of Object.values(game.players)) {
      if (target.id === player.id || target.team === player.team || target.status !== 'alive') continue;
      if (dist(player.position, target.position) <= def.ult.radius) {
        applyDamage(player, target, def.ult.damage, 'ult');
        target.stunned = true;
        target.stunnedUntil = t + def.ult.stunDuration;
      }
    }
    game.events.push({ type: 'ult', casterId: player.id, name: 'quake' });
  } else if (player.heroClass === 'dps') {
    // Snipe — spawn piercing bullet
    spawnProjectile(player.id, 'snipe', player.position.x, player.position.y, player.angle, 700, def.ult.damage, true);
    game.events.push({ type: 'ult', casterId: player.id, name: 'snipe' });
  } else if (player.heroClass === 'healer') {
    // Revive all dead allies
    let revived = 0;
    for (const target of Object.values(game.players)) {
      if (target.team !== player.team || target.status !== 'dead') continue;
      const def2 = HERO_DEFS[target.heroClass];
      const teamPlayers = Object.values(game.players).filter(q => q.team === target.team && q.id !== target.id && q.status === 'alive').length;
      const spawn = SPAWNS[target.team][Math.min(teamPlayers, 2)];
      target.status = 'alive';
      target.hp = Math.floor(def2.maxHp * def.ult.healPercent);
      target.position = { x: spawn.x + (Math.random() * 40 - 20), y: spawn.y + (Math.random() * 40 - 20) };
      revived++;
    }
    game.events.push({ type: 'ult', casterId: player.id, name: 'revive', revived });
  }
}

// ─── Healer beam (continuous) ─────────────────────────────────────────────────
function processHealerBeam(player, dt) {
  if (player.heroClass !== 'healer') return;
  if (player.status !== 'alive') return;
  const def = HERO_DEFS.healer.basic;
  const t = now();
  if (t < player.skills.basicCd) return;

  // Find nearest ally and nearest enemy within range
  let nearestAlly = null, nearestAllyDist = Infinity;
  let nearestEnemy = null, nearestEnemyDist = Infinity;

  for (const target of Object.values(game.players)) {
    if (target.id === player.id || target.status !== 'alive') continue;
    const d = dist(player.position, target.position);
    if (d > def.range) continue;
    if (target.team === player.team) {
      if (d < nearestAllyDist) { nearestAllyDist = d; nearestAlly = target; }
    } else {
      if (d < nearestEnemyDist) { nearestEnemyDist = d; nearestEnemy = target; }
    }
  }

  const dtSec = dt / 1000;
  if (nearestAlly) {
    const healAmount = def.healPerSec * dtSec;
    applyHeal(nearestAlly, healAmount);
    game.events.push({ type: 'beam', casterId: player.id, targetId: nearestAlly.id, effect: 'heal' });
  }
  if (nearestEnemy && !wallBlocksLine(player.position.x, player.position.y, nearestEnemy.position.x, nearestEnemy.position.y)) {
    const dmg = def.damagePerSec * dtSec;
    applyDamage(player, nearestEnemy, Math.ceil(dmg), 'beam');
  }
}

// ─── Main game tick ───────────────────────────────────────────────────────────
let lastTickTime = Date.now();

function tick() {
  const t = now();
  const dt = t - lastTickTime;
  lastTickTime = t;

  if (game.phase === 'waiting') {
    // nothing
    return;
  }

  if (game.phase === 'countdown') {
    const elapsed = t - game.countdownStart;
    if (elapsed >= COUNTDOWN_DURATION_MS) {
      startPlaying();
    } else {
      const remaining = Math.ceil((COUNTDOWN_DURATION_MS - elapsed) / 1000);
      broadcast({ type: 'gameState', phase: 'countdown', countdown: remaining });
    }
    return;
  }

  if (game.phase === 'ended') return;

  // playing
  game.tick++;
  game.events = [];

  const timeLeft = GAME_DURATION_MS - (t - game.startTime);
  if (timeLeft <= 0) {
    // Time up — determine winner by score
    const s0 = game.scores[0] || 0;
    const s1 = game.scores[1] || 0;
    endGame(s0 >= s1 ? 0 : 1);
    return;
  }

  // Update timed status effects
  for (const p of Object.values(game.players)) {
    if (p.shield && t >= p.shieldExpiry) p.shield = false;
    if (p.invincible && t >= p.invincibleExpiry) p.invincible = false;
    if (p.stunned && t >= p.stunnedUntil) p.stunned = false;
    if (p.status === 'dead' && t >= p.respawnAt) {
      respawnPlayer(p);
      game.events.push({ type: 'respawn', playerId: p.id });
    }
  }

  // Process healer beams
  for (const p of Object.values(game.players)) {
    if (p.heroClass === 'healer') processHealerBeam(p, dt);
  }

  // Move projectiles
  const projDt = dt / 1000;
  const toDelete = [];
  for (const [pid, proj] of Object.entries(game.projectiles)) {
    proj.position.x += Math.cos(proj.angle) * proj.speed * projDt;
    proj.position.y += Math.sin(proj.angle) * proj.speed * projDt;

    // Out of bounds
    if (proj.position.x < 0 || proj.position.x > MAP_W || proj.position.y < 0 || proj.position.y > MAP_H) {
      toDelete.push(pid);
      continue;
    }

    // Wall collision
    let hitWall = false;
    for (const w of WALLS) {
      if (circleRect(proj.position.x, proj.position.y, 6, w.x, w.y, w.w, w.h)) {
        hitWall = true;
        break;
      }
    }
    if (hitWall) { toDelete.push(pid); continue; }

    const owner = game.players[proj.ownerId];

    // Hit players
    for (const target of Object.values(game.players)) {
      if (target.status !== 'alive') continue;
      if (proj.hit.has(target.id)) continue;

      // Friendly fire check
      if (proj.type === 'healorb') {
        if (!owner || target.team !== owner.team) continue;
      } else {
        if (owner && target.team === owner.team) continue;
      }

      if (dist(proj.position, target.position) <= 20) {
        proj.hit.add(target.id);

        if (proj.type === 'healorb') {
          applyHeal(target, proj.data.heal || 80);
          game.events.push({ type: 'hit', attackerId: proj.ownerId, targetId: target.id, dmg: 0, skillType: 'healorb' });
          if (!proj.pierce) { toDelete.push(pid); break; }
        } else {
          applyDamage(owner, target, proj.damage, proj.type);
          if (!proj.pierce) { toDelete.push(pid); break; }
        }
      }
    }
  }

  for (const pid of toDelete) delete game.projectiles[pid];

  // Build snapshot
  const snapshot = {
    type: 'snapshot',
    tick: game.tick,
    players: Object.values(game.players).map(p => ({
      id: p.id,
      heroClass: p.heroClass,
      position: { x: Math.round(p.position.x), y: Math.round(p.position.y) },
      angle: p.angle,
      hp: Math.max(0, Math.round(p.hp)),
      maxHp: p.maxHp,
      status: p.status,
      respawnAt: p.respawnAt,
      ultCharge: Math.round(p.ultCharge),
      shield: p.shield,
      invincible: p.invincible,
      stunned: p.stunned,
      skills: {
        skill1Cd: Math.max(0, p.skills.skill1Cd - now()),
        skill2Cd: Math.max(0, p.skills.skill2Cd - now()),
      },
      team: p.team,
    })),
    projectiles: Object.values(game.projectiles).map(proj => ({
      id: proj.id,
      ownerId: proj.ownerId,
      type: proj.type,
      position: { x: Math.round(proj.position.x), y: Math.round(proj.position.y) },
      angle: proj.angle,
      speed: proj.speed,
    })),
    events: game.events,
    gameState: {
      phase: game.phase,
      timeLeft: Math.max(0, Math.round(timeLeft)),
      scores: { ...game.scores },
    },
  };

  broadcast(snapshot);
}

// ─── Express + HTTP ────────────────────────────────────────────────────────────
const app = express();
app.use(express.static(path.join(__dirname, 'public')));

app.get('/health', (req, res) => {
  res.json({ ok: true, phase: game.phase, players: Object.keys(game.players).length });
});

const server = http.createServer(app);

// ─── WebSocket server ─────────────────────────────────────────────────────────
const wss = new WebSocketServer({ server });

wss.on('connection', (ws, req) => {
  const playerId = 'p_' + uuidv4().slice(0, 8);
  console.log(`[WS] Connect: ${playerId}`);

  // Assign team (balance teams)
  const team0 = Object.values(game.players).filter(p => p.team === 0).length;
  const team1 = Object.values(game.players).filter(p => p.team === 1).length;
  const team = team0 <= team1 ? 0 : 1;

  // Default hero — will be overridden by client's chosen hero
  const player = createPlayer(playerId, 'dps', team, ws);
  game.players[playerId] = player;

  // Send init
  send(ws, {
    type: 'init',
    playerId,
    team,
    walls: WALLS,
    mapSize: { w: MAP_W, h: MAP_H },
    heroes: Object.keys(HERO_DEFS),
  });

  if (game.phase === 'waiting') tryStartCountdown();
  if (game.phase === 'playing' || game.phase === 'countdown') {
    send(ws, { type: 'gameState', phase: game.phase });
  }

  ws.on('message', (data) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }

    if (msg.type === 'selectHero') {
      const heroClass = msg.heroClass;
      if (!HERO_DEFS[heroClass]) return;
      if (game.phase !== 'waiting' && game.phase !== 'countdown') return;
      const def = HERO_DEFS[heroClass];
      player.heroClass = heroClass;
      player.hp = def.maxHp;
      player.maxHp = def.maxHp;
      console.log(`[Game] ${playerId} selected ${heroClass}`);
      return;
    }

    // Input message
    const { seq, timestamp, move, aim, action } = msg;
    if (seq !== undefined) player.lastSeq = seq;

    if (!player || player.status !== 'alive' || game.phase !== 'playing') return;
    if (player.stunned) return;

    const t = now();
    const def = HERO_DEFS[player.heroClass];

    // Movement (server-authoritative)
    if (move && (move.x !== 0 || move.y !== 0)) {
      // Normalize
      const len = Math.sqrt(move.x * move.x + move.y * move.y) || 1;
      const nx = move.x / len;
      const ny = move.y / len;
      const speed = def.speed;
      const dtMove = TICK_MS / 1000; // use tick rate for movement step
      player.position.x += nx * speed * dtMove;
      player.position.y += ny * speed * dtMove;
      resolveWallCollision(player.position, 16);
    }

    // Aim angle validation (0 to 2π)
    if (typeof aim === 'number' && isFinite(aim)) {
      player.angle = ((aim % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    }

    // Actions
    if (action && action.type) {
      switch (action.type) {
        case 'basic':  handleBasic(player, t);  break;
        case 'skill1': handleSkill1(player, t); break;
        case 'skill2': handleSkill2(player, t); break;
        case 'ult':    handleUlt(player, t);    break;
      }
    }
  });

  ws.on('close', () => {
    console.log(`[WS] Disconnect: ${playerId}`);
    delete game.players[playerId];
    if (Object.keys(game.players).length === 0 && game.phase !== 'ended') {
      game.phase = 'waiting';
    }
  });

  ws.on('error', (err) => {
    console.error(`[WS] Error ${playerId}:`, err.message);
  });
});

// ─── Tick loop ────────────────────────────────────────────────────────────────
setInterval(tick, TICK_MS);

// ─── Start ────────────────────────────────────────────────────────────────────
server.listen(PORT, () => {
  console.log(`[Server] Overwatch game server running on port ${PORT}`);
  console.log(`[Server] Tick rate: ${TICK_RATE}/s, Map: ${MAP_W}x${MAP_H}`);
  console.log(`[Server] Heroes: ${Object.keys(HERO_DEFS).join(', ')}`);
  console.log(`[Server] Walls: ${WALLS.length} obstacles defined`);
});

module.exports = { app, server };
