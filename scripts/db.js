#!/usr/bin/env node
/**
 * db.js — Leaflet DB dev utility
 *
 * Commands:
 *   node db.js schema          Dump full live schema to supabase_schema/*.json
 *   node db.js update          Apply supabase_schema/update.sql, then refresh schema
 *   node db.js update --dry    Print SQL without executing
 *
 * Env (from ../.env or shell):
 *   SUPABASE_DB_URL            Full connection string (preferred)
 *   SUPABASE_DB_HOST/PORT/DATABASE/USER/PASSWORD  Individual credentials
 *   SUPABASE_DB_SSL=require    Require TLS (default for Supabase)
 *   SUPABASE_SUPER_ADMIN_EMAIL Replaces {{SUPABASE_SUPER_ADMIN_EMAIL}} in update.sql
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promises as fs } from 'node:fs';
import { open } from 'node:fs/promises';
import { Client } from 'pg';
import dotenv from 'dotenv';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const SCHEMA_DIR = path.join(ROOT, 'supabase_schema');
const SCHEMA_SQL = path.join(SCHEMA_DIR, 'getSchemaDump.sql');
const UPDATE_SQL = path.join(SCHEMA_DIR, 'update.sql');

dotenv.config({ path: path.join(ROOT, '.env') });

// ─── Section → output file map (mirrors getSchemaDump.sql section comments) ─
const SECTION_FILES = {
  1: 'tables.json',
  2: 'columns.json',
  3: 'constraints.json',
  4: 'indexes.json',
  5: 'triggers.json',
  6: 'views_ddl.json',
  7: 'functions_ddl.json',
  8: 'sequences_ddl.json',
  9: 'current_RLS.json',
};

// ─── Error codes that are safe to ignore ─────────────────────────────────────
const DUPLICATE_CODES = new Set(['42701','42710','42711','42712','42723','42P04','42P06','42P07']);
const MISSING_CODES   = new Set(['42703','42704','42P01','42P02','42P03']);

// ─── Helpers ─────────────────────────────────────────────────────────────────
async function atomicWrite(filePath, data) {
  const tmp = `${filePath}.tmp`;
  const fh = await open(tmp, 'w');
  try {
    await fh.writeFile(data, 'utf8');
    await fh.sync();
  } finally {
    await fh.close();
  }
  await fs.rename(tmp, filePath);
}

function buildClientConfig() {
  const url = process.env.SUPABASE_DB_URL;
  const ssl =
    (process.env.SUPABASE_DB_SSL || '').toLowerCase() === 'require' ||
    (url && url.includes('sslmode=require'))
      ? { rejectUnauthorized: false }
      : undefined;

  if (url) return { connectionString: url, ssl };

  const { SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_DATABASE,
          SUPABASE_DB_USER, SUPABASE_DB_PASSWORD } = process.env;
  if (!SUPABASE_DB_HOST) {
    throw new Error('Missing DB config. Set SUPABASE_DB_URL or SUPABASE_DB_HOST + credentials.');
  }
  return {
    host: SUPABASE_DB_HOST,
    port: SUPABASE_DB_PORT ? Number(SUPABASE_DB_PORT) : 5432,
    database: SUPABASE_DB_DATABASE,
    user: SUPABASE_DB_USER,
    password: SUPABASE_DB_PASSWORD,
    ssl,
  };
}

// Parse /* N) ... */ sections from getSchemaDump.sql
function parseSchemaSections(sql) {
  const sections = [];
  const re = /\/\*\s*(\d+)\)\s*[^]*?\*\/\s*([\s\S]*?);/g;
  let m;
  while ((m = re.exec(sql)) !== null) {
    const n = Number(m[1]);
    if (SECTION_FILES[n]) sections.push({ n, query: m[2].trim() });
    else console.warn(`getSchemaDump.sql: no output mapping for section ${n}, skipping.`);
  }
  if (!sections.length) throw new Error('No SQL sections found in getSchemaDump.sql');
  return sections.sort((a, b) => a.n - b.n);
}

// Robust SQL splitter: handles quotes, $$ dollar-quoting, -- and /* */ comments
function splitSQL(sql) {
  const stmts = [];
  let cur = '', inSingle = false, inDouble = false, inLine = false,
      inBlock = false, dollar = null;
  for (let i = 0; i < sql.length; i++) {
    const c = sql[i], n = sql[i + 1] ?? '';
    if (inLine)  { cur += c; if (c === '\n') inLine  = false; continue; }
    if (inBlock) { cur += c; if (c === '*' && n === '/') { cur += n; i++; inBlock = false; } continue; }
    if (dollar)  { if (sql.startsWith(dollar, i)) { cur += dollar; i += dollar.length - 1; dollar = null; } else cur += c; continue; }
    if (inSingle){ cur += c; if (c === "'" && n === "'") { cur += n; i++; } else if (c === "'") inSingle = false; continue; }
    if (inDouble){ cur += c; if (c === '"' && n === '"') { cur += n; i++; } else if (c === '"') inDouble = false; continue; }
    if (c === '-' && n === '-') { cur += c + n; i++; inLine  = true; continue; }
    if (c === '/' && n === '*') { cur += c + n; i++; inBlock = true; continue; }
    if (c === '$') { const tag = sql.slice(i).match(/^\$[A-Za-z0-9_]*\$/)?.[0]; if (tag) { cur += tag; i += tag.length - 1; dollar = tag; continue; } }
    if (c === "'") { inSingle = true; cur += c; continue; }
    if (c === '"') { inDouble = true; cur += c; continue; }
    if (c === ';') { const s = cur.trim(); if (s) stmts.push(s); cur = ''; continue; }
    cur += c;
  }
  const tail = cur.trim();
  if (tail) stmts.push(tail);
  return stmts;
}

function isSafeToIgnore(err, stmt) {
  if (!err?.code) return false;
  if (DUPLICATE_CODES.has(err.code)) return true;
  if (MISSING_CODES.has(err.code)) {
    const up = stmt.trim().toUpperCase();
    return up.startsWith('DROP') ||
      (up.startsWith('ALTER TABLE') && (up.includes('DROP COLUMN') || up.includes('DROP CONSTRAINT') || up.includes('DROP INDEX')));
  }
  return false;
}

// ─── Commands ─────────────────────────────────────────────────────────────────

async function cmdSchema(client) {
  await fs.mkdir(SCHEMA_DIR, { recursive: true });
  const sql = await fs.readFile(SCHEMA_SQL, 'utf8');
  const sections = parseSchemaSections(sql);
  const summary = [];

  for (const { n, query } of sections) {
    const result = await client.query(query);
    const rows = result.rows ?? [];
    await atomicWrite(path.join(SCHEMA_DIR, SECTION_FILES[n]), JSON.stringify(rows, null, 2) + '\n');
    summary.push({ section: n, file: SECTION_FILES[n], rows: rows.length });
  }

  console.log('\nSchema dump complete:');
  console.table(summary.map(r => ({ Section: r.section, File: r.file, Rows: r.rows })));
}

async function cmdUpdate(client, dry) {
  let sql = '';
  try {
    sql = await fs.readFile(UPDATE_SQL, 'utf8');
  } catch (e) {
    if (e.code === 'ENOENT') { console.log('update.sql not found — nothing to apply.'); return; }
    throw e;
  }

  const superAdminEmail = process.env.SUPABASE_SUPER_ADMIN_EMAIL;
  const PLACEHOLDER = '{{SUPABASE_SUPER_ADMIN_EMAIL}}';
  if (sql.includes(PLACEHOLDER)) {
    if (!superAdminEmail) throw new Error('SUPABASE_SUPER_ADMIN_EMAIL must be set to apply update.sql');
    sql = sql.replaceAll(PLACEHOLDER, superAdminEmail);
  }

  const trimmed = sql.trim();
  if (!trimmed) { console.log('update.sql is empty — nothing to apply.'); return; }

  const stmts = splitSQL(trimmed);
  if (!stmts.length) { console.log('No statements found in update.sql.'); return; }

  if (dry) {
    console.log(`\n[dry-run] ${stmts.length} statement(s) would be executed:\n`);
    stmts.forEach((s, i) => console.log(`  [${i + 1}] ${s.split('\n')[0].slice(0, 80)}...`));
    return;
  }

  console.log(`Executing ${stmts.length} statement(s)...`);
  const remaining = [];
  for (let i = 0; i < stmts.length; i++) {
    const stmt = stmts[i];
    try {
      await client.query(stmt);
      console.log(`  ✓ [${i + 1}] ${stmt.split('\n')[0].slice(0, 70)}`);
    } catch (err) {
      if (isSafeToIgnore(err, stmt)) {
        console.warn(`  ↷ [${i + 1}] skipped (${err.code}): ${stmt.split('\n')[0].slice(0, 60)}`);
      } else {
        // Save remaining statements back to update.sql so nothing is lost
        remaining.push(...stmts.slice(i));
        const rollback = remaining.map(s => `${s};`).join('\n\n')
          .replaceAll(superAdminEmail ?? '', PLACEHOLDER);
        await atomicWrite(UPDATE_SQL, rollback + '\n');
        console.error(`\n✗ Failed at statement [${i + 1}]: ${stmt.split('\n')[0]}`);
        console.error(`  Error: ${err.message?.split('\n')[0]}`);
        console.error(`  Remaining ${remaining.length} statement(s) written back to update.sql`);
        throw err;
      }
    }
  }

  // Refresh schema snapshots
  console.log('\nRefreshing schema snapshots...');
  await cmdSchema(client);

  // Clear update.sql
  await atomicWrite(UPDATE_SQL, '');
  console.log('\nDone. update.sql cleared.');
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  const [,, cmd, flag] = process.argv;
  const dry = flag === '--dry';

  if (!cmd || cmd === '--help' || cmd === '-h') {
    console.log([
      '',
      'db.js — Leaflet DB dev utility',
      '',
      '  node db.js schema          Dump full schema → supabase_schema/*.json',
      '  node db.js update          Apply update.sql, then refresh schema',
      '  node db.js update --dry    Print SQL without executing',
      '',
      'Requires SUPABASE_DB_URL (or host credentials) in ../.env',
      '',
    ].join('\n'));
    process.exit(0);
  }

  if (cmd !== 'schema' && cmd !== 'update') {
    console.error(`Unknown command: ${cmd}\nRun with --help for usage.`);
    process.exit(1);
  }

  const client = new Client(buildClientConfig());
  try {
    await client.connect();
    if (cmd === 'schema') await cmdSchema(client);
    if (cmd === 'update') await cmdUpdate(client, dry);
  } finally {
    await client.end();
  }
}

main().catch(err => {
  console.error(err.message ?? err);
  process.exit(1);
});
