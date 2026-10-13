# Site Copier

Saves a website you own as local files you can edit, which helps when you've lost the original source code.

## Setup (one time)
```
pip install requests beautifulsoup4
```

## Use
```
python copy_site.py https://your-old-site.com my-site --max-pages 50
```
Then open `my-site/index.html` in your browser.

## Editing
- Click **Edit** (bottom-right) and type directly on the page.
- Click **Save** to download the edited page. Put it back in the `my-site` folder, replacing the old file.
- To change colors or layout, edit the CSS files in `my-site/assets/`.

## Limits
- Server code and databases (logins, forms, checkout) can't be copied. You only get what the browser sees.
- Sites that build pages with JavaScript (React and similar) may come out mostly empty.
- Only copy sites you own or have permission to copy.
