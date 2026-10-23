import unittest

from chapterdl.chapters import chapter_number, format_number, select_chapters
from chapterdl.parser import Selector, extract_images, extract_links

PAGE = """
<html><head><base href="/base/"></head><body>
<div class="menu"><img src="/logo.png"><a href="/series/chapter-1/">One</a></div>
<div id="reader" class="wrap">
  <div class="inner">
    <img src="data:image/gif;base64,AAAA" data-src=" 1.jpg ">
    <img src="2.PNG">
    <img data-lazy-src="//cdn.example.com/3.webp">
    <img srcset="small.jpg 1x, large.jpg 2x">
    <img src="2.PNG">
    <img src="icon.svg">
    <noscript><img src="1.jpg"></noscript>
  </div>
  <a href="chapter-2/#comments">Next <b>chapter</b></a>
</div>
<img src="/footer.jpg">
</body></html>
"""


class SelectorTests(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(Selector.parse("#reader"), Selector(None, "reader", ()))
        self.assertEqual(Selector.parse("div.a.b"), Selector("div", None, ("a", "b")))

    def test_rejects_unsupported(self):
        for text in ("", "div > img", "a[href]"):
            with self.assertRaises(ValueError):
                Selector.parse(text)


class ExtractImageTests(unittest.TestCase):
    url = "https://site.test/series/chapter-3/"

    def test_whole_page(self):
        images = extract_images(PAGE, self.url)
        self.assertEqual(
            images,
            [
                "https://site.test/logo.png",
                "https://site.test/base/1.jpg",
                "https://site.test/base/2.PNG",
                "https://cdn.example.com/3.webp",
                "https://site.test/base/large.jpg",
                "https://site.test/footer.jpg",
            ],
        )

    def test_container_by_id_and_class(self):
        expected = [
            "https://site.test/base/1.jpg",
            "https://site.test/base/2.PNG",
            "https://cdn.example.com/3.webp",
            "https://site.test/base/large.jpg",
        ]
        self.assertEqual(extract_images(PAGE, self.url, Selector.parse("#reader")), expected)
        self.assertEqual(extract_images(PAGE, self.url, Selector.parse("div.wrap")), expected)

    def test_any_extension(self):
        html = '<div id="r"><img src="/media/42"></div>'
        self.assertEqual(extract_images(html, self.url, Selector.parse("#r")), [])
        self.assertEqual(
            extract_images(html, self.url, Selector.parse("#r"), extensions=None),
            ["https://site.test/media/42"],
        )


class LinkTests(unittest.TestCase):
    def test_links_are_resolved_and_defragmented(self):
        links = extract_links(PAGE, "https://site.test/series/", Selector.parse("#reader"))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].url, "https://site.test/base/chapter-2/")
        self.assertEqual(links[0].text, "Next chapter")


class ChapterTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(chapter_number("https://x.test/a/chapter-12/"), 12.0)
        self.assertEqual(chapter_number("https://x.test/a/chapter-12-5/"), 12.5)
        self.assertEqual(chapter_number("https://x.test/a/chapter-7.5"), 7.5)
        self.assertIsNone(chapter_number("https://x.test/about/"))

    def test_format(self):
        self.assertEqual(format_number(7), "0007")
        self.assertEqual(format_number(12.5), "0012.5")

    def test_selection_sorting_and_range(self):
        html = "".join(
            f'<a href="/s/chapter-{n}/">c</a>' for n in (10, 2, 1, 2, 3)
        ) + '<a href="/s/about/">x</a><a href="/s/chapter-x/">y</a><a href="/s/chapter-1/feed/">f</a>'
        links = extract_links(html, "https://x.test/s/")
        chapters, unnumbered = select_chapters(links, start=2, end=10)
        self.assertEqual([c.number for c in chapters], [2, 3, 10])
        self.assertEqual(unnumbered, ["https://x.test/s/chapter-x/"])


if __name__ == "__main__":
    unittest.main()
