// deploy/ecosystem.config.js — PM2 process definition for the DEVDEPARTMENT
// autopilot supervisor (Wave B, T1 "Watchtower" topology).
//
// See docs/DEPLOY_CLAWSRV.md for the full setup guide. Quick reference:
//   pm2 start deploy/ecosystem.config.js
//   pm2 save
//   pm2 startup
//   pm2 logs devteam-<project>
//
// IMPORTANT: replace <PROJECT_PLACEHOLDER> below with your actual project
// name (e.g. "orb-terminal") before starting — PM2 process names must be
// unique per project if you're running the supervisor for more than one
// DEVDEPARTMENT-managed project on the same host.
//
// Credentials (DEVTEAM_TG_TOKEN, DEVTEAM_TG_CHAT, etc.) are injected via a
// PM2 env file or `pm2 set`, NEVER hardcoded into this file — this file is
// expected to be committed to the project's own repo (or kept alongside it),
// so anything secret here would leak. See docs/DEPLOY_CLAWSRV.md § Secrets.
module.exports = {
  apps: [
    {
      name: "devteam-<PROJECT_PLACEHOLDER>",
      script: "scripts/supervisor.py",
      interpreter: "python3",
      args: "--loop --interval 300",
      cwd: __dirname + "/..",
      autorestart: true,
      max_restarts: 10,
      min_uptime: "30s",
      restart_delay: 5000,
      // Exponential-ish backoff is PM2's default once max_restarts is hit
      // within its window; min_uptime guards against a fast-crash loop
      // burning through all 10 restarts in seconds.
      watch: false,
      // stdout/stderr land in PM2's own log store by default; `pm2 logs
      // devteam-<project>` tails both. Uncomment to also write flat files:
      // out_file: "./logs/devteam-<PROJECT_PLACEHOLDER>-out.log",
      // error_file: "./logs/devteam-<PROJECT_PLACEHOLDER>-err.log",
      env: {
        // DEVTEAM_TG_TOKEN, DEVTEAM_TG_CHAT: set via `pm2 set` or a PM2
        // ecosystem env file (e.g. `pm2 start ... --env production` with a
        // separate `env_production` block referencing a .env you source
        // before `pm2 start`), never committed here.
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
