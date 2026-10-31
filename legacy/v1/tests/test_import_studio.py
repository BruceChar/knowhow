import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.ingest.studio import (
    analyze_source,
    apply_transforms,
    compile_template,
    preview_import,
    suggest_mapping,
)


class ImportStudioTests(unittest.TestCase):
    def test_transforms_are_declarative(self):
        value = apply_transforms("  jc-ab 12 ", ["trim", "uppercase", "remove_spaces"])
        self.assertEqual(value, "JC-AB12")
        self.assertEqual(
            apply_transforms("$1.27\nbased on 3000pcs", ["number"]),
            1.27,
        )

    def test_product_csv_auto_mapping_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "products.csv"
            path.write_text(
                "型号,中文名,英文名,材质\nJC-1,泡沫盒,Foam Box,PP\n",
                encoding="utf-8",
            )
            analysis = analyze_source(path)
            headers = analysis["sheets"][0]["headers"]
            mapping = suggest_mapping(headers, "product")
            self.assertEqual(mapping["sku_code"], "型号")
            config = {
                "target": "product",
                "fields": [
                    {"from": "型号", "to": "sku_code", "required": True,
                     "transforms": ["trim", "uppercase"]},
                    {"from": "中文名", "to": "name_zh", "required": True},
                    {"from": "材质", "to": "spec.material"},
                ],
            }
            preview = preview_import(path, config)
            self.assertEqual(preview["ready_rows"], 1)
            self.assertEqual(preview["error_rows"], 0)
            self.assertEqual(
                preview["sample"][0]["normalized"]["sku_code"], "JC-1"
            )

    def test_preview_rejects_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "faq.csv"
            path.write_text(
                "question,answer\nSame question,Answer A\nSame question,Answer B\n",
                encoding="utf-8",
            )
            config = {
                "target": "faq",
                "fields": [
                    {"from": "question", "to": "question", "required": True},
                    {"from": "answer", "to": "answer", "required": True},
                ],
            }
            preview = preview_import(path, config)
            self.assertEqual(preview["ready_rows"], 1)
            self.assertEqual(preview["error_rows"], 1)

    def test_product_import_builds_variant_and_offer_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quote.csv"
            path.write_text(
                "SKU,英文名,颜色,报价,币种,有效期\n"
                "JC-1,Pet Bowl,Blue,12.5,USD,2026-12-31\n",
                encoding="utf-8",
            )
            config = {
                "config_key": "tenant_quote",
                "target": "product",
                "fields": [
                    {"from": "SKU", "to": "sku_code", "required": True},
                    {"from": "英文名", "to": "name_en"},
                    {"from": "颜色", "to": "variant.attributes.color"},
                    {"from": "报价", "to": "offer.unit_price",
                     "transforms": ["trim", "number"]},
                    {"from": "币种", "to": "offer.currency",
                     "transforms": ["trim", "currency"]},
                    {"from": "有效期", "to": "offer.valid_until",
                     "transforms": ["trim", "date"]},
                ],
            }
            preview = preview_import(path, config)
            self.assertEqual(preview["ready_rows"], 1)
            row = preview["sample"][0]["normalized"]
            self.assertEqual(row["variant"]["variant_code"], "JC-1")
            self.assertEqual(row["variant"]["attributes"]["color"], "Blue")
            self.assertEqual(row["offer"]["unit_price"], 12.5)
            self.assertEqual(row["offer"]["currency"], "USD")
            self.assertEqual(row["offer"]["valid_until"], "2026-12-31")

    def test_offer_requires_currency_and_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quote.csv"
            path.write_text("SKU,报价\nJC-1,12.5\n", encoding="utf-8")
            config = {
                "target": "product",
                "fields": [
                    {"from": "SKU", "to": "sku_code", "required": True},
                    {"from": "报价", "to": "offer.unit_price",
                     "transforms": ["trim", "number"]},
                ],
            }
            preview = preview_import(path, config)
            errors = preview["sample"][0]["errors"]
            self.assertIn("报价缺少币种 currency", errors)
            self.assertIn("报价缺少有效期 valid_until", errors)

    def test_analyze_and_select_only_product_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.xlsx"
            with pd.ExcelWriter(path) as writer:
                pd.DataFrame([
                    {"型号": "JC-1", "英文名": "Pet Bowl", "价格": "9.9"},
                ]).to_excel(writer, sheet_name="产品", index=False)
                pd.DataFrame([
                    {"说明": "本页是编辑说明，不应导入"},
                ]).to_excel(writer, sheet_name="说明", index=False)
            analysis = analyze_source(path)
            product_sheet = next(s for s in analysis["sheets"]
                                 if s["sheet"] == "产品")
            suggestions = suggest_mapping(product_sheet["headers"], "product")
            self.assertEqual(suggestions["offer.unit_price"], "价格")
            config = {
                "target": "product",
                "fields": [
                    {"from": "型号", "to": "sku_code", "required": True},
                    {"from": "英文名", "to": "name_en"},
                ],
            }
            preview = preview_import(
                path, config, selected_sheets=["产品"], limit=10
            )
            self.assertEqual(preview["total_rows"], 1)
            self.assertEqual(preview["sample"][0]["sheet"], "产品")

    def test_override_adds_missing_target_field(self):
        compiled = compile_template(
            {"target": "product",
             "fields": [{"from": "型号", "to": "sku_code"}]},
            overrides={"offer.unit_price": "价格"},
        )
        self.assertIn(
            "offer.unit_price",
            {f["to"] for f in compiled["fields"]},
        )


if __name__ == "__main__":
    unittest.main()
