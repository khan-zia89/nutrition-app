# My Nutrition – personal calorie and nutrition app

Runs on your own Windows computer. No login, no hosting, no paid services.

- Type a meal in plain English. The app finds each food and asks only about what's missing.
- Calories, protein, carbohydrate, fat and fibre (plus sugars, saturates and salt where known).
- An ingredient-by-ingredient breakdown. Change any gram value and everything recalculates.
- A daily diary with day totals.
- Barcode lookup through Open Food Facts, or type in a nutrition label.
- Optional food-photo suggestions from a free AI model running on your computer (Ollama).

**How numbers are produced:** nutrition values come only from the food databases or a label you
confirmed. The arithmetic is done by plain code in `core/engine.py`. AI (if you turn it on) only
reads words or photos, and never supplies a nutrition value.

## 1. Install (once)

1. Install **Python 3.13** from https://www.python.org/downloads/windows/ .
   On the first installer screen, tick **"Add python.exe to PATH"**.
2. Unzip this folder somewhere simple, e.g. `C:\Users\<you>\Documents\nutrition_app`.
3. Double-click **`install.bat`**. It takes a few minutes the first time.

## 2. Get the food databases (once, all free)

Save these files in the `data` folder:

| File | Where | Notes |
|---|---|---|
| CoFID 2021 Excel (.xlsx) | https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid | UK foods, about 2,900 items |
| SR Legacy, CSV (April 2018) | https://fdc.nal.usda.gov/download-datasets | US foods, about 7,800 items, with portion weights. Keep it zipped |
| Foundation Foods, CSV (latest) | same page | Newer US analyses. Keep it zipped |

## 3. Run

Double-click **`start_app.bat`**. Your browser opens at http://localhost:8501.
The first time, go to **Data & settings** and press **Import CoFID** and **Import USDA**.
To stop the app, close the black window.

## 4. Optional: photo recognition and smarter text reading (free, local)

1. Install Ollama from https://ollama.com/download (Windows).
2. Open Command Prompt and run `ollama pull gemma3:4b` (about 3.3 GB; needs roughly 8 GB of RAM).
3. Restart the app. The **Photo of a meal** page now works. In **Data & settings** you can also
   tick "Use a local AI model" for reading meal text.

## Sharing it online (free, Streamlit Community Cloud)

The same files run online in a "try-it" mode: food data loads automatically, each visitor gets a
private diary that lasts only while their tab is open, and the photo page is hidden (it needs Ollama
on your own PC). You switch this mode on with a secret in step 4, so your own copy is unaffected.

1. Unzip a fresh copy of this app into a new folder (not the one you use, so that your diary,
   `.venv` and settings are not uploaded). Copy your CoFID `.xlsx` and USDA `SR Legacy` zip into
   its `data` folder.
2. On github.com, create a free account, then **New repository**. Name it e.g. `nutrition-app`,
   choose **Public**, and click **Create repository**. Then click **uploading an existing file**,
   drag everything from the new folder into the page (including `.streamlit`, `core`, `tests`, `data`),
   and click **Commit changes**.
3. Go to share.streamlit.io, choose **Continue with GitHub**, and allow access.
4. Click **Create app**, then **Yup, I have an app**. Pick your repository, branch `main` and file
   `app.py`, and choose an App URL. Open **Advanced settings**, choose Python 3.13, and paste this
   into **Secrets**:
   ```
   public_mode = true
   contact_email = "your.email@example.com"
   ```
   Click **Save**, then **Deploy**.
5. The first start takes a few minutes. Then share the `https://….streamlit.app` link.

Free-plan notes: the app goes to sleep after about 12 hours with no visitors (the next visitor
clicks to wake it), the code and data files are public, and nothing saved online is kept after a restart.

## Folder map

```
app.py                 the screens (Streamlit)
core/engine.py         all calculations (pure arithmetic, fully tested)
core/parser.py         reads meal text (rules; optional local AI)
core/units.py          converts g / oz / tbsp / cups / "1 large" to grams
core/foods.py          searches the database
core/importers.py      loads CoFID and USDA files
core/openfoodfacts.py  barcode lookup
core/barcode.py        reads barcodes from photos
core/vision.py         optional photo suggestions via Ollama
core/diary.py          your food diary
core/database.py       the SQLite database (data/nutrition.db)
tests/                 automated tests (double-click run_tests.bat)
```

## Troubleshooting

- **"python is not recognized"**: reinstall Python with "Add python.exe to PATH" ticked.
- **The browser shows "can't connect"**: wait a few seconds and refresh. The app takes a moment to start.
- **The import fails**: check the file is the CoFID .xlsx or a FoodData Central *CSV* zip (not JSON).
  The error message names what's missing.
- **No barcode found**: hold the pack flat, fill the frame and avoid glare, or type the number.
- **Back up your diary** by copying `data/nutrition.db`.

## Data credits and licences

- UK CoFID: Public Health England / OHID, McCance and Widdowson's Composition of Foods Integrated Dataset 2021 (gov.uk download).
- USDA FoodData Central: U.S. Department of Agriculture, Agricultural Research Service. Public domain (CC0).
- Open Food Facts: openfoodfacts.org, Open Database Licence (ODbL). Products are stored separately and labelled.

Estimates only, not medical advice.
