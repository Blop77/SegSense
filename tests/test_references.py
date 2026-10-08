"""Tavily reference lookup, tested against a local stand-in for the Tavily API."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from segsense import references
from segsense.references import Tavily, classify
from segsense.sanitizer import SanitizerReport


def report(kind: str) -> SanitizerReport:
    return SanitizerReport("AddressSanitizer", kind, kind)


class FakeTavily(BaseHTTPRequestHandler):
    requests = []
    RESULTS = {
        "cwe.mitre.org": [
            {"title": "CWE - CWE-416: Use After Free (4.20)", "url": "https://cwe.mitre.org/data/definitions/416.html",
             "content": "| | Home > CWE List > CWE-416: Use After Free | --- | The product reuses or references memory after it has been freed."},
            {"title": "CWE Version 4.16", "url": "https://cwe.mitre.org/data/published/cwe_v4.16.pdf", "content": "pdf"},
        ],
        "wiki.sei.cmu.edu": [
            {"title": "MEM30-C. Do not access freed memory", "url": "https://wiki.sei.cmu.edu/confluence/display/c/MEM30-C",
             "content": "| Tool | Version | Checker | --- --- | 25.10 | csa-use-after-free |"},
            {"title": "not a web link", "url": "javascript:alert(1)", "content": "x"},
        ],
    }

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTavily.requests.append((self.headers.get("Authorization"), body))
        payload = json.dumps({"results": self.RESULTS[body["include_domains"][0]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class ReferenceTests(unittest.TestCase):
    def test_bug_kinds_map_to_cwe_and_cert_rules(self):
        w = classify(report("heap-use-after-free"))
        self.assertEqual((w.cwe, w.cert_rule), ("CWE-416", "MEM30-C"))
        self.assertEqual(classify(report("double-free")).cwe, "CWE-415")
        self.assertEqual(classify(report("detected memory leaks")).cert_rule, "MEM31-C")
        self.assertEqual(classify(report("signed integer overflow: 479001600 * 13 cannot be represented")).cwe, "CWE-190")
        self.assertIsNone(classify(report("something new")))

    def test_lookup_finds_the_cwe_entry_and_the_cert_rule(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTavily)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/search"
        FakeTavily.requests.clear()

        with mock.patch.object(references, "TAVILY_URL", url):
            found = Tavily(api_key="tvly-test").lookup(report("heap-use-after-free"))

        queries = {body["include_domains"][0]: body["query"] for _, body in FakeTavily.requests}
        self.assertEqual(queries["cwe.mitre.org"], "CWE-416 Use After Free")
        self.assertEqual(queries["wiki.sei.cmu.edu"], "SEI CERT C MEM30-C Do not access freed memory")
        self.assertTrue(all(auth == "Bearer tvly-test" for auth, _ in FakeTavily.requests))
        # The canonical pages are kept; the PDF and the non-web link are dropped.
        self.assertEqual([r.url for r in found.references],
                         ["https://cwe.mitre.org/data/definitions/416.html", "https://wiki.sei.cmu.edu/confluence/display/c/MEM30-C"])
        self.assertEqual(found.cert_rule, "MEM30-C. Do not access freed memory")
        # Navigation debris is stripped; a snippet that is only a table is dropped entirely.
        self.assertEqual(found.references[0].snippet, "The product reuses or references memory after it has been freed.")
        self.assertEqual(found.references[1].snippet, "")

    def test_from_env_is_optional(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(references.from_env())
        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-x"}):
            self.assertIsInstance(references.from_env(), Tavily)


if __name__ == "__main__":
    unittest.main()
