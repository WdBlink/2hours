#!/usr/bin/env python3
"""Rasterize the approved vector mark for the native app and menu bar."""
import base64
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    assets = Path(__file__).resolve().parents[1] / "assets/brand"
    variants = [("app-icon.svg", "icon.png", 1024),
                ("mark.svg", "MenuIconTemplate.png", 16),
                ("mark.svg", "MenuIconTemplate@2x.png", 32)]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for source, target, size in variants:
            page.set_viewport_size({"width": size, "height": size})
            svg = base64.b64encode((assets / source).read_bytes()).decode()
            page.set_content(f'<style>html,body{{margin:0;background:transparent}}img{{display:block;width:100vw;height:100vh}}</style><img src="data:image/svg+xml;base64,{svg}" alt="Two Hours">')
            page.locator("img").evaluate("image => image.decode()")
            png = page.screenshot(path=str(assets / target), omit_background=True)
            assert struct.unpack(">IIBB", png[16:26]) == (size, size, 8, 6), "Icon must be an RGBA PNG at the requested size"
        browser.close()
    print("Built Two Hours app and menu icons from approved SVGs")


if __name__ == "__main__":
    main()
