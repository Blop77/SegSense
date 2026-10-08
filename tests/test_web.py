import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from segsense import web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler("nvidia/patch", "nvidia/triage"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.server.shutdown)
        self.addCleanup(setattr, web, "EXAMPLES_ONLY", web.EXAMPLES_ONLY)

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return json.loads(resp.read())

    def post(self, source):
        req = urllib.request.Request(self.base + "/api/fix", json.dumps({"source": source}).encode(), method="POST")
        return urllib.request.urlopen(req)

    def test_config_and_examples(self):
        self.assertEqual(self.get("/api/config")["patch_model"], "nvidia/patch")
        self.assertIn("heap_overflow.c", self.get("/api/examples"))

    def test_examples_only_rejects_custom_code(self):
        web.EXAMPLES_ONLY = True
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('int main(void) { system("rm -rf /"); }')
        self.assertEqual(ctx.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
