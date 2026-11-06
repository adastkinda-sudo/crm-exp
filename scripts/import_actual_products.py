#!/usr/bin/env python3
"""Import a financial_recommender product folder into the platform."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from catalog import load_products  # noqa: E402
from database import init_db  # noqa: E402


def main() -> None:
    source = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "tmp" / "financial_recommender"
    products_file = source / "products.json"
    images_source = source / "images"
    if not products_file.exists() or not images_source.is_dir():
        raise SystemExit(f"数据目录不完整：{source}")

    products = load_products(products_file)
    images = []
    for product in products:
        image = (source / product["image_path"]).resolve()
        if source.resolve() not in image.parents or not image.is_file():
            raise SystemExit(f"商品 {product['id']} 缺少图片")
        with image.open("rb") as file:
            if file.read(8) != b"\x89PNG\r\n\x1a\n":
                raise SystemExit(f"商品 {product['id']} 图片不是有效的 PNG 文件")
        images.append((image, f"P{product['id']:02d}.png"))

    (ROOT / "data" / "actual_products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assets = ROOT / "frontend" / "assets" / "products"
    assets.mkdir(parents=True, exist_ok=True)
    for image, filename in images:
        shutil.copy2(image, assets / filename)

    sources = source / "DATA_SOURCES.md"
    if sources.exists():
        shutil.copy2(sources, ROOT / "data" / "actual_products_sources.md")

    init_db()
    print(f"已导入 {len(products)} 条商品记录和 {len(images)} 张商品图片")


if __name__ == "__main__":
    main()
