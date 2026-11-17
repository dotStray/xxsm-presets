# XXSM presets

The default resource repository for a certain mod manager.

Kept up to date by a human who plays all three of these games, and an automated system that pulls data
from upstream (see [Sources](#sources)).

This repository, the builder included, was entirely vibe coded: written with AI assistance.

## Sources

| | Genshin Impact | Honkai: Star Rail | Zenless Zone Zero |
|---|---|---|---|
| Hashes | [SilentNightSound/GI-Model-Importer-Assets](https://github.com/SilentNightSound/GI-Model-Importer-Assets) (GPL-3.0) | [Astute4533/SR-Model-Importer-Assets](https://github.com/Astute4533/SR-Model-Importer-Assets) (no licence stated) | [leotorrez/ZZ-Model-Importer-Assets](https://github.com/leotorrez/ZZ-Model-Importer-Assets) (GPL-3.0) |
| Portraits | [gachabase](https://gi.gachabase.net) | [gachabase](https://hsr.gachabase.net) | [gachabase](https://zzz.gachabase.net) |
| Game icon | [Google Play](https://play.google.com/store/apps/details?id=com.miHoYo.GenshinImpact&gl=US) | [Google Play](https://play.google.com/store/apps/details?id=com.HoYoverse.hkrpgoversea&gl=US) | [Google Play](https://play.google.com/store/apps/details?id=com.HoYoverse.Nap&gl=US) |

Portraits are game art © COGNOSPHERE / HoYoverse.

## Zenless identity corrections

`overrides/zenless.json` keeps Sunbringer as one playable agent, not three agents or
an agent with two costumes. The source entries `avatar:1601`, `avatar:1602`,
`avatar:1603`, `avatar:1604` and `avatar:1606` all join the established `Sunbringer`
identity. Lindverne and Dracaene (internally CampusLead) are constituent forms.

- `partOf` assigns separately named Lindverne, CampusLead and Dracaene hash folders
  to Sunbringer when they arrive; their hashes are combined rather than discarded.
- The display name stays Sunbringer even if the source changes its entry order.
  Alternate form names remain aliases. Source attributes are combined by the normal
  multi-entry join; the portrait still comes from the first joined source entry.
- `retired` permits removing only the mistaken standalone Lindverne and CampusLead
  entries. Both had pending hashes and no hash records when corrected; Sunbringer's
  established ID and mod-folder name remain unchanged. Aliases do not move local
  mod directories.
