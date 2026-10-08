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

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTavily.requests.append((self.headers.get("Authorization"), body))
        results = [
            {"title": "CWE-416: Use After Free", "url": "https://cwe.mitre.org/data/definitions/416.html",
             "content": "The product reuses or references memory after it has been freed.", "score": 0.9},
            {"title": "not a web link", "url": "javascript:alert(1)", "content": "x"},
        ]
        payload = json.dumps({"query": body["query"], "results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class ReferenceTests(unittest.TestCase):
    def test_bug_kinds_map_to_cwe(self):
        self.assertEqual(classify(report("heap-use-after-free")), ("CWE-416", "Use After Free"))
        self.assertEqual(classify(report("double-free"))[0], "CWE-415")
        self.assertEqual(classify(report("detected memory leaks"))[0], "CWE-401")
        self.assertEqual(classify(report("signed integer overflow: 479001600 * 13 cannot be represented"))[0], "CWE-190")
        self.assertEqual(classify(report("something new")), (None, "something new"))

    def test_lookup_queries_trusted_sources_and_drops_non_web_links(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTavily)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/search"

        with mock.patch.object(references, "TAVILY_URL", url):
            found = Tavily(api_key="tvly-test").lookup(report("heap-use-after-free"))

        auth, body = FakeTavily.requests[-1]
        self.assertEqual(auth, "Bearer tvly-test")
        self.assertIn("CWE-416 Use After Free", body["query"])
        self.assertIn("cwe.mitre.org", body["include_domains"])
        self.assertEqual(found.cwe, "CWE-416")
        self.assertEqual([r.url for r in found.references], ["https://cwe.mitre.org/data/definitions/416.html"])

    def test_from_env_is_optional(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(references.from_env())
        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-x"}):
            self.assertIsInstance(references.from_env(), Tavily)


if __name__ == "__main__":
    unittest.main()
