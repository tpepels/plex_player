import ssl
import unittest
from unittest.mock import MagicMock, patch

from services.http_client import get


class HttpClientTests(unittest.TestCase):
    @staticmethod
    def _response():
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"ok"
        response.headers = {}
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    @patch("services.http_client.urlopen")
    @patch("services.http_client.ssl.create_default_context")
    def test_can_disable_tls_verification_for_explicit_local_plex_use(
        self, create_default_context, urlopen
    ):
        context = MagicMock()
        create_default_context.return_value = context
        urlopen.return_value = self._response()

        response = get(
            "https://192.168.1.200:32400/status/sessions",
            verify_tls=False,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        self.assertIs(urlopen.call_args.kwargs["context"], context)

    @patch("services.http_client.urlopen")
    @patch("services.http_client.ssl.create_default_context")
    def test_tls_verification_stays_enabled_by_default(
        self, create_default_context, urlopen
    ):
        urlopen.return_value = self._response()

        response = get("https://example.com/status")

        self.assertEqual(response.status_code, 200)
        create_default_context.assert_not_called()
        self.assertNotIn("context", urlopen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
