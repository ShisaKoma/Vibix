from html.parser import HTMLParser
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase


class IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        if tag == "svg":
            self.icons.append(dict(attrs))


class ActionAccessibilityTests(SimpleTestCase):
    def test_shared_icons_are_decorative_and_not_focusable(self):
        parser = IconParser()
        parser.feed(
            Template('{% load ui %}{% ui_icon "edit" %}{% module_label "pages" %}').render(
                Context()
            )
        )
        self.assertEqual(len(parser.icons), 2)
        for icon in parser.icons:
            self.assertEqual(icon["aria-hidden"], "true")
            self.assertEqual(icon["focusable"], "false")

    def test_compact_actions_keep_a_text_label_and_native_semantics(self):
        import re

        templates = Path(__file__).resolve().parents[2] / "templates"
        actions = 0
        for path in templates.rglob("*.html"):
            for match in re.finditer(
                r'<(a|button)\b[^>]*class="[^"]*\bicon-action\b[^"]*"[^>]*>(.*?)</\1>',
                path.read_text(),
                re.DOTALL,
            ):
                with self.subTest(template=str(path), action=match.group(0)):
                    self.assertIn('class="cta-label"', match.group(2))
                    self.assertNotIn('aria-hidden="true"', match.group(2).split("cta-label")[1])
                    self.assertNotIn('tabindex="-1"', match.group(0))
                    if match.group(1) == "a":
                        self.assertIn("href=", match.group(0))
                actions += 1
        self.assertGreater(actions, 5)
