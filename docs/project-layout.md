# Project layout

_Moved from the old CLAUDE.md ("Project layout")._

```
describer/
  CLAUDE.md                # this file: current state and rules
  README.md                # for a person installing and running it
  requirements.txt         # runtime and dev deps together
  pyproject.toml           # ruff and pytest settings
  config.example.yaml      # documented defaults; copy to config.yaml
  .env.example             # the credential names; .env itself is never committed
  .claude/launch.json      # the dev server for preview tools; clears RTT_TOKEN
  docs/design/             # one note per addendum: why things are the way they are
  describer/
    main.py                # FastAPI app, SSE endpoint, static + admin routes
    config.py              # load/validate/save YAML (pydantic models)
    schedule.py            # display on/off schedule
    profiles.py            # which profile is in force, and merging it in
    stationlist.py         # builds web/static/stations.json from NaPTAN; run by hand
    setup.py               # is first-run setup needed; the key test; the wizard's answers
    credentials.py         # write-only API keys from /admin
    updater.py             # checks GitHub for a newer release, installs on request
    system.py              # Shut down / Restart from /admin: injectable, Pi-only
    weather.py             # Open-Meteo forecasts: cache, refresh loop
    rail/
      base.py              # RailSource protocol, RailApiError
      models.py            # Service, Board, CallingPoint, Position, Formation
      ldbws.py             # Rail Data Marketplace (Darwin) client
      rtt.py               # Realtime Trains client
      sources.py           # holds both clients, fails over, recovers
      poller.py            # background polling, staleness, backoff, filters
    announce/
      phrasing.py          # builds announcement text from Service
      tts.py               # Piper wrapper + cache + playback
      scheduler.py         # decides what to announce and when
    web/static/
      index.html           # the one DOM every theme fills
      base.css             # structure and the sizing model; themes add the look
      board.js             # SSE client, renders the DOM, calls into the theme
      admin.html  admin.css  admin.js
      setup.html  setup.css  setup.js   # the phone's first-run wizard
      stationsearch.js     # the station combobox, shared by admin and setup
      setup/               # screenshots for the key walkthrough (Graham's to add)
      stations.json        # every station's CRS, name and coordinates
      fonts/               # self-hosted; only bedstead.woff2 ships
      themes/              # <theme>.css + <theme>.js for each of the seven
        dotmatrix.js       # the shared 5x7 dot font; not a theme
        colours.js         # writes the configured palette; not a theme
  deploy/
    install.sh             # root-run, two-part: provision (chroot-safe), configure
    describer.service      # systemd *system* unit for the backend, User=describer
    kiosk.service          # systemd *system* unit for cage + chromium, User=describer
    apply-deploy.sh        # root oneshot body: installs deploy/ from its own GitHub clone
    describer-apply-deploy.service  # systemd *system* unit, oneshot, User=root
    polkit/
      50-describer.rules   # describer may: restart describer.service, start
                           # describer-apply-deploy.service, power off, reboot
    shutdown_button.py     # six presses on GPIO21 in 10 s -> poweroff; system python
    shutdown-button.service  # systemd *system* unit for the above
  tests/
    conftest.py            # autouse guards: no credentials, no network, no update checks
    fixtures/              # recorded LDBWS, RTT, NaPTAN and Open-Meteo responses
```
