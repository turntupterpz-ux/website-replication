#!/usr/bin/env python3
"""Save a website you own as local, editable files.

Usage:
    python copy_site.py https://example.com [output_folder] [--max-pages 20]

Downloads the HTML pages on the same domain (up to --max-pages), plus their
CSS, JavaScript, images and fonts, and rewrites every link so the copy works
offline. Each saved page gets a small "Edit" toolbar so you can change text in
the browser and save the result.

Only use this on websites you own or have permission to copy.
"""
import argparse
import hashlib
import os
import re
import sys
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (site-copier; personal backup)"}
CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I)
CSS_IMPORT = re.compile(r"""@import\s+(['"])(.*?)\1""", re.I)

EDITOR_SNIPPET = """
<div id="__site_editor" contenteditable="false" style="position:fixed;bottom:16px;right:16px;z-index:2147483647;
  display:flex;gap:8px;font:14px/1 system-ui,sans-serif;">
  <button id="__edit_toggle" style="padding:10px 14px;border:0;border-radius:6px;background:#222;color:#fff;cursor:pointer">Edit</button>
  <button id="__edit_save" style="padding:10px 14px;border:0;border-radius:6px;background:#2a8a2a;color:#fff;cursor:pointer">Save</button>
</div>
<script id="__site_editor_js">
(function () {
  var on = false, btn = document.getElementById("__edit_toggle");
  btn.onclick = function () {
    on = !on;
    document.designMode = on ? "on" : "off";
    btn.textContent = on ? "Stop editing" : "Edit";
    btn.style.background = on ? "#c0392b" : "#222";
  };
  document.getElementById("__edit_save").onclick = function () {
    document.designMode = "off";
    var copy = document.documentElement.cloneNode(true);
    ["#__site_editor", "#__site_editor_js"].forEach(function (s) {
      var el = copy.querySelector(s); if (el) el.remove();
    });
    var html = "<!DOCTYPE html>\\n" + copy.outerHTML;
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    a.download = location.pathname.split("/").pop() || "index.html";
    a.click();
    if (on) document.designMode = "on";
  };
})();
</script>
"""


class SiteCopier:
    def __init__(self, start_url, out_dir, max_pages, log=print):
        self.log = log
        self.cancelled = False
        self.start = urldefrag(start_url)[0]
        self.host = urlparse(self.start).netloc
        self.out = os.path.abspath(out_dir)
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.assets = {}  # absolute url -> local path relative to out dir
        self.pages = {}   # absolute url -> local html filename

    # ---------- helpers ----------
    def fetch(self, url):
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            self.log(f"  ! skipped {url}: {e}")
            return None

    def page_name(self, url):
        if url not in self.pages:
            path = urlparse(url).path.strip("/")
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", path) or "index"
            if not name.endswith(".html"):
                name += ".html"
            if name in self.pages.values():
                name = name[:-5] + "_" + hashlib.md5(url.encode()).hexdigest()[:6] + ".html"
            self.pages[url] = name
        return self.pages[url]

    def is_page(self, url):
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.netloc != self.host:
            return False
        ext = os.path.splitext(p.path)[1].lower()
        return ext in ("", ".html", ".htm", ".php", ".asp", ".aspx")

    def asset_path(self, url):
        p = urlparse(url)
        path = p.path.lstrip("/") or "file"
        if path.endswith("/"):
            path += "index"
        path = re.sub(r"[^A-Za-z0-9._/-]+", "_", path)
        if p.query:
            root, ext = os.path.splitext(path)
            path = f"{root}_{hashlib.md5(p.query.encode()).hexdigest()[:6]}{ext}"
        return os.path.join("assets", re.sub(r"[^A-Za-z0-9.-]", "_", p.netloc), path)

    def save(self, rel, data):
        full = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    @staticmethod
    def rel(target, from_file):
        return os.path.relpath(target, os.path.dirname(from_file) or ".").replace(os.sep, "/")

    # ---------- assets ----------
    def get_asset(self, url):
        """Download an asset once; return its local path (relative to out dir)."""
        if self.cancelled:
            return None
        url = urldefrag(url)[0]
        if url in self.assets:
            return self.assets[url]
        local = self.asset_path(url)
        self.assets[url] = local
        r = self.fetch(url)
        if r is None:
            self.assets[url] = None
            return None
        data = r.content
        if local.endswith(".css") or "text/css" in r.headers.get("Content-Type", ""):
            data = self.rewrite_css(r.text, url, local).encode("utf-8")
        self.save(local, data)
        self.log(f"  + {url}")
        return local

    def rewrite_css(self, css, base_url, css_local):
        def swap(match, group):
            ref = match.group(group).strip()
            if not ref or ref.startswith(("data:", "#")):
                return match.group(0)
            local = self.get_asset(urljoin(base_url, ref))
            if not local:
                return match.group(0)
            new = self.rel(local, css_local)
            return match.group(0).replace(ref, new)
        css = CSS_IMPORT.sub(lambda m: swap(m, 2), css)
        return CSS_URL.sub(lambda m: swap(m, 2), css)

    # ---------- pages ----------
    def copy_page(self, url, queue):
        r = self.fetch(url)
        if r is None or "html" not in r.headers.get("Content-Type", "html"):
            return
        self.log(f"Page: {url}")
        page_file = self.page_name(url)
        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup.find_all("base"):
            tag.decompose()

        # Single-URL attributes on assets
        for tag, attr in [("link", "href"), ("script", "src"), ("img", "src"),
                          ("source", "src"), ("video", "src"), ("video", "poster"),
                          ("audio", "src"), ("input", "src"), ("embed", "src")]:
            for el in soup.find_all(tag):
                ref = el.get(attr)
                if not ref or ref.startswith(("data:", "javascript:", "#")):
                    continue
                if tag == "link":
                    rels = " ".join(el.get("rel", [])).lower()
                    if not any(k in rels for k in ("stylesheet", "icon", "preload", "manifest")):
                        continue
                local = self.get_asset(urljoin(url, ref))
                if local:
                    el[attr] = self.rel(local, page_file)
                for a in ("integrity", "crossorigin"):
                    el.attrs.pop(a, None)

        # Lazy-loaded images and srcset
        for el in soup.find_all(["img", "source"]):
            for lazy in ("data-src", "data-lazy-src", "data-original"):
                if el.get(lazy) and not el.get(lazy).startswith("data:"):
                    local = self.get_asset(urljoin(url, el[lazy]))
                    if local:
                        el["src"] = self.rel(local, page_file)
            for attr in ("srcset", "data-srcset"):
                if el.get(attr):
                    parts = []
                    for item in el[attr].split(","):
                        bits = item.strip().split()
                        if not bits:
                            continue
                        local = self.get_asset(urljoin(url, bits[0]))
                        if local:
                            bits[0] = self.rel(local, page_file)
                        parts.append(" ".join(bits))
                    el[attr] = ", ".join(parts)

        # Inline <style> blocks and style="" attributes
        for el in soup.find_all("style"):
            if el.string:
                el.string = self.rewrite_css(el.string, url, page_file)
        for el in soup.find_all(style=True):
            el["style"] = self.rewrite_css(el["style"], url, page_file)

        # Links to other pages on the same site
        for a in soup.find_all("a", href=True):
            target, frag = urldefrag(urljoin(url, a["href"]))
            if self.is_page(target):
                if target not in self.pages and len(self.pages) < self.max_pages:
                    queue.append(target)
                if target in self.pages or len(self.pages) < self.max_pages:
                    a["href"] = self.page_name(target) + (f"#{frag}" if frag else "")
            elif a["href"].startswith("/"):
                a["href"] = target  # keep external-to-copy links absolute

        body = soup.body or soup
        body.append(BeautifulSoup(EDITOR_SNIPPET, "html.parser"))
        self.save(page_file, str(soup).encode("utf-8"))

    def run(self):
        os.makedirs(self.out, exist_ok=True)
        queue = deque([self.start])
        self.page_name(self.start)
        done = set()
        while queue and not self.cancelled:
            url = queue.popleft()
            if url in done:
                continue
            done.add(url)
            self.copy_page(url, queue)
        # Make sure the first page is reachable as index.html
        first = self.pages[self.start]
        if first != "index.html" and not os.path.exists(os.path.join(self.out, "index.html")):
            with open(os.path.join(self.out, "index.html"), "w") as f:
                f.write(f'<meta http-equiv="refresh" content="0; url={first}">')
        saved = sum(1 for v in self.assets.values() if v)
        self.log(f"\nDone: {len(done)} page(s), {saved} file(s) saved to {self.out}")
        self.log(f"Open {os.path.join(self.out, 'index.html')} in your browser.")


def main():
    ap = argparse.ArgumentParser(description="Copy a website you own into editable local files.")
    ap.add_argument("url", help="Website address, e.g. https://example.com")
    ap.add_argument("out", nargs="?", default="copied-site", help="Output folder (default: copied-site)")
    ap.add_argument("--max-pages", type=int, default=20, help="Max pages to copy (default: 20)")
    args = ap.parse_args()
    url = args.url if "://" in args.url else "https://" + args.url
    SiteCopier(url, args.out, max(1, args.max_pages)).run()


if __name__ == "__main__":
    sys.exit(main())
