<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/describer-mark-dark.svg">
  <img src="docs/img/describer-mark-light.svg" alt="Describer" width="280">
</picture>

# Your station. On your wall.

A live UK train departure board for a Raspberry Pi and a TV. I built it because I wanted to
know whether to run for the 07:42 without picking up my phone.

It shows one or two National Rail stations full screen, in twelve themes from split-flap to
Ceefax, and can announce trains out loud like a platform does.

`NO TERMINAL` · `NO SOLDERING` · `ABOUT 30 MINUTES`

---

## `01` What you need

| | |
|---|---|
| `PI` | Raspberry Pi 4 Model B, with its official power supply |
| `CARD` | microSD card, 16 GB or larger |
| `SCREEN` | Any TV or monitor with HDMI, and a micro-HDMI to HDMI cable |
| `NETWORK` | Wi-Fi, and its name and password |
| `COMPUTER` | A Mac or PC with a card reader, to write the card once |
| `KEY` | A free rail data key. Step `02` gets you one |

---

## `02` Get your rail data key

The board gets its train times from the Rail Data Marketplace, the rail industry's own data
service. It is free. Everyone brings their own key, and it takes about five minutes.

1. Go to [raildata.org.uk](https://raildata.org.uk), choose **Register** and create an account.
2. Sign in and search for **Live Arrival and Departure Boards**.
3. Check the name. There is a departures-only product that looks almost the same, and the
   board can't use it. You want the one with **Arrival** in the title.
4. Choose **Subscribe**. It's free.
5. Open your subscription and copy the **Consumer key**.

Keep that key somewhere you can paste from on your phone: a note, or an email to yourself.
You'll need it in step `05`.

> Treat the key like a password. The board stores it on the Pi and never shows it again.

---

## `03` Write the card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer and
   put the microSD card in.
2. Open Imager. Under **App Options → Content repository**, paste this address:

   ```
   https://raw.githubusercontent.com/grahamlehr/describer-image/imager/os_list.json
   ```

3. Choose **Raspberry Pi 4** as the device, then **Describer** as the operating system.
4. Choose your card.
5. When Imager offers its settings, fill them in:

   | | |
   |---|---|
   | `HOSTNAME` | `describer` |
   | `WI-FI` | Your network's name and password, exactly as on your phone |
   | `USERNAME` | Anything except `describer`. Your own first name is fine |
   | `PASSWORD` | Your choice. Write it down. You won't need it day to day |

6. Write the card. It takes a few minutes.

> Don't skip the settings, and don't pick the image file with **Use custom**. Either way the
> Pi starts with no Wi-Fi and asks for a username on the TV.

---

## `04` Plug it in

1. Put the card in the Pi.
2. Connect the TV first, to the micro-HDMI socket nearest the power socket.
3. Then connect the power.

The first start takes a few minutes. When it's ready, the TV says
**Set up your departure board** and shows a QR code.

---

## `05` Set it up from your phone

Put your phone on the same Wi-Fi as the board and scan the QR code with its camera. No QR
reader? Open `describer.local:8080/setup` in a browser.

| | |
|---|---|
| `01` Station | Type a name, pick it, and choose departures or arrivals. Add a second station if you want two side by side |
| `02` Rail data key | Paste the key from step `02`. **Test key** tells you your next train if it works |
| `03` Sound | Announcements on or off, through the TV or the headphone socket |
| `04` Finish | The board appears on the TV within seconds |

Done.

---

## `06` Everyday use

Everything else lives on one page. Open it from any phone or computer on the same Wi-Fi:

```
http://describer.local:8080/admin
```

| | |
|---|---|
| `THEMES` | Twelve, switchable live. Try `splitflap`, `1990s` and `victorian` |
| `STATIONS` | Change them, show only your platforms, or hide trains you couldn't walk to in time |
| `SOUND` | Announcements, their timing, and recorded voices |
| `SCHEDULE` | Turn the screen off overnight, or show a different station in the morning rush |
| `UPDATES` | The board checks for new versions. **Status → Updates → Update and restart** installs one |
| `POWER` | **Status → Power → Shut down** before you unplug it. Pulling the power on a running Pi can corrupt the card |

---

## `07` Optional: a backup data source

If the Rail Data Marketplace has a bad day, the board can switch to
[Realtime Trains](https://www.realtimetrains.co.uk) by itself and switch back afterwards.
It's free for personal use, and you can add it any time.

1. Go to [api-portal.rtt.io](https://api-portal.rtt.io) and choose **Sign in with RTT**.
   Create a Realtime Trains account if you don't have one.
2. In the portal, request an **API token** on the free plan and copy it.
3. Open `/admin` on the board and go to **Data sources → Credentials**.
4. Paste it into **Realtime Trains token** and choose **Save keys**.
5. On the same tab, set **Fallback** to **Realtime Trains** and save.

The free plan allows 1,000 requests a day. The board paces itself to stay inside that, so
use the token on one board only.

---

## `08` If something isn't right

| On the TV | What to do |
|---|---|
| **Please enter new username** | The card was written without Imager's settings. Write it again, following step `03` |
| Nothing at all | Check the TV is on the right HDMI input, and the cable is in the Pi's micro-HDMI socket nearest the power socket |
| The QR code, but your phone can't open the page | Check the phone is on the same Wi-Fi. Then type the address in numbers, shown under the QR code |
| The QR code, with no address in numbers under it | The board isn't on your Wi-Fi. The name or password was probably mistyped: write the card again |
| **Your rail data key was not accepted** | Check you subscribed to Live **Arrival and** Departure Boards, then paste the key again at `/setup` |
| **No services at this time** | Nothing is wrong. There are no trains for a while |
| A blank screen | Check the schedule in `/admin`. The screen may be set to sleep |

Still stuck? [Open an issue](https://github.com/grahamlehr/describer/issues) and tell me what
the TV says.

---

## `09` Going further

| | |
|---|---|
| [Configuration](docs/configuration.md) | Every option: stations, themes, colours, weather, announcements, schedule, profiles |
| [Recorded voices](docs/voice-pack.md) | Make the board speak in a recorded voice |
| [Installing by hand](docs/pi-install-and-upkeep.md) | Your own Pi OS install, services, updating by command line, the shutdown button |
| [Development](docs/development.md) | Running it on a Mac or Linux, how it fits together, tests |
| [describer-image](https://github.com/grahamlehr/describer-image) | How the card image is built |

---

`CREDITS` Train data from the [Rail Data Marketplace](https://raildata.org.uk) and
[Realtime Trains](https://www.realtimetrains.co.uk). Weather by
[Open-Meteo.com](https://open-meteo.com/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The station list contains public
sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Describer is a personal project and is not affiliated with National Rail or any train operator.
