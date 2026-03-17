# Plexamp LCD Setup (Raspberry Pi Zero 2 W + JOY-IT RB-TFT3.2-V3)
Deze gids beschrijft stap-voor-stap hoe je het 3.2" LCD scherm gebruikt om
Plexamp album art te tonen, en een klok + weer wanneer er niets speelt. De
scripts staan al in:
``` /home/tom/setup_plexlcd.sh /home/tom/plexlcd.py
```
---
# 1. Scripts uitvoerbaar maken
```bash cd /home/tom ls -l setup_plexlcd.sh plexlcd.py chmod +x
setup_plexlcd.sh plexlcd.py ``` ---
# 2. Installeer de driver voor het LCD scherm
Het Joy-IT scherm gebruikt de bekende `LCD-show` drivers. ```bash sudo rm
-rf LCD-show git clone https://github.com/goodtft/LCD-show.git chmod -R
755 LCD-show cd LCD-show sudo ./LCD32-show ``` De Pi zal nu automatisch
herstarten. ---
# 3. Controleer of het scherm werkt
Na de reboot: ```bash ls -l /dev/fb* fbset -s ``` Je zou normaal
**`/dev/fb1`** moeten zien. ---
# 4. Test het scherm met een afbeelding
Installeer een simpele framebuffer image viewer. ```bash sudo apt update
sudo apt install -y fbi ``` Test: ```bash sudo fbi -T 1 -d /dev/fb1 -a
/usr/share/rpd-wallpaper/road.jpg ``` Als het scherm een afbeelding toont
werkt de driver correct. Stop met: ``` Ctrl + C ``` ---
# 5. Plex Token ophalen
Open Plex in je browser: ``` http://192.168.1.200:32400/web ```
### Methode
1. Open developer tools (F12) 2. Ga naar **Network** 3. Reload de pagina
4. Klik een request naar bijvoorbeeld: ``` /library /status/sessions ```
5. Zoek naar: ``` X-Plex-Token=xxxxxxxxxxxx ``` Kopieer die token. ---
# 6. Player name vinden
Start Plexamp op de Pi en laat muziek spelen. Run op de Pi: ```bash curl
"http://192.168.1.200:32400/status/sessions?X-Plex-Token=JOUW_TOKEN" ```
Zoek in de output naar: ``` Player: title="Plexamp" device="Raspberry Pi"
state="playing" ``` De waarde van **title** is de player name.
Bijvoorbeeld: ``` Plexamp Pi Zero ``` ---
# 7. Setup script gebruiken
Ga terug naar je home directory. ```bash cd /home/tom ``` Installeer
dependencies: ```bash ./setup_plexlcd.sh install ``` Configureer: ```bash
./setup_plexlcd.sh configure ``` Je moet invullen:
| Setting | Value | ------|------| Plex server |
| `http://192.168.1.200:32400` | Plex token | jouw token | Player name |
| exacte player title | Latitude | `41.1579` | Longitude | `-8.6291` |
| Timezone | `Europe/Lisbon` | Framebuffer | `/dev/fb1` | Width | `320` |
| Height | `240` |
---
# 8. Controleer actieve Plex players
Start een nummer in Plexamp en run: ```bash ./setup_plexlcd.sh test ```
Dit toont alle actieve players. Gebruik de **exacte naam** die hier
verschijnt. ---
# 9. Controleer framebuffer
```bash ./setup_plexlcd.sh fb ``` Output moet ongeveer zijn: ``` /dev/fb1
``` ---
# 10. Test de Python app handmatig
Laad eerst de configuratie: ```bash set -a source /home/tom/plexlcd/.env
set +a ``` Run daarna: ```bash python3 /home/tom/plexlcd/plexlcd.py ```
Het scherm moet nu: - album art tonen tijdens muziek - klok + weer tonen
wanneer niets speelt ---
# 11. Installeer de service (autostart)
Als alles werkt: ```bash ./setup_plexlcd.sh service ``` Controleer:
```bash systemctl status plexlcd.service ``` ---
# 12. Rotatie aanpassen (indien nodig)
Als het scherm gedraaid staat: ```bash cd /home/tom/LCD-show sudo
./rotate.sh 90 ``` Opties: ``` 0 90 180 270 ``` ---
# Klaar 🎵
De Pi zal nu automatisch: - Plex controleren - album art tonen tijdens
afspelen - klok + weer tonen wanneer idle
- automatisch starten bij boot