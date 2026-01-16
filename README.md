# AlgoEnergy web

Statický web (HTML/CSS) pro AlgoEnergy.cz.

## GitHub Pages – rychlý postup

1. **Nahrajte obsah repozitáře na GitHub** (např. `algo-energy-web`).
2. V **Settings → Pages** nastavte:
   - **Build and deployment** → *Deploy from a branch*
   - **Branch** → `main` (nebo `work`) a **/ (root)**
3. V sekci **Custom domain** zadejte `algoenergy.cz`.
4. Po uložení GitHub vytvoří `CNAME` a v UI zobrazí DNS instrukce.

### DNS ve Wedos (doporučené)

- Pro kořenovou doménu `algoenergy.cz` nastavte **A záznamy** na IP GitHub Pages:
  - `185.199.108.153`
  - `185.199.109.153`
  - `185.199.110.153`
  - `185.199.111.153`
- Pro `www.algoenergy.cz` nastavte **CNAME** na `<vas-username>.github.io`.

> Poznámka: IP adresy GitHub Pages jsou oficiální a dlouhodobě stabilní, ale
> GitHub může doporučení upravit – v takovém případě se řiďte jejich UI.

## Lokální spuštění

```bash
python -m http.server 8000
```

Poté otevřete `http://localhost:8000/index.html`.
