module.exports = {
  apps: [
    {
      name: "parserclients",
      script: "main.py",
      interpreter: "./.venv/bin/python",
      cwd: "/home/deploy/parserclients",
      autorestart: true,
      max_restarts: 10,
      min_uptime: "15s",
      kill_timeout: 15000,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
