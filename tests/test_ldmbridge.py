"""testing via trial

    run `trial` from root directory
"""
from twisted.internet.test.reactormixins import ReactorBuilder

from pyldm import ldmbridge


def get_filepath(name):
    """Helper function to get the text file contents"""
    # basedir = os.path.dirname(__file__)
    return f"../testdata/{name}"


class MyProductIngestor(ldmbridge.LDMProductReceiver):
    """hacky"""

    hits = 0

    def process_data(self, data):
        """Process data"""
        self.hits = self.hits + 1

    def connectionLost(self, reason):
        """Stop"""
        # print("Processed %s bytes" % (self.bytes_received, ))
        self.reactor.stop()


class StdioFilesTests(ReactorBuilder):
    """."""

    def test_nwwsoi_dedup(self):
        """Can we DEDUP a file from NWWS-OI with multiple duplicates"""
        reactor = self.buildReactor()
        ingest = MyProductIngestor(dedup=True)
        with open(get_filepath("nwwsoi_example.txt"), "rb") as f:
            ldmbridge.LDMProductFactory(
                ingest, stdin=f.fileno(), reactor=reactor
            )

        self.runReactor(reactor)
        assert ingest.hits == 1

    def test_deduplicate(self):
        """."""
        reactor = self.buildReactor()
        ingest = MyProductIngestor(dedup=True)
        with open(get_filepath("twoprods.txt"), "rb") as f:
            ldmbridge.LDMProductFactory(
                ingest, stdin=f.fileno(), reactor=reactor
            )

        self.runReactor(reactor)
        assert ingest.hits == 1

    def test_bridge(self):
        """."""
        reactor = self.buildReactor()
        ingest = MyProductIngestor()
        with open(get_filepath("twoprods.txt"), "rb") as f:
            ldmbridge.LDMProductFactory(
                ingest, stdin=f.fileno(), reactor=reactor
            )

        self.runReactor(reactor)
        assert ingest.hits == 2

    def test_binarydata(self):
        """."""
        reactor = self.buildReactor()
        ingest = MyProductIngestor()
        with open(get_filepath("threeNIDS.txt"), "rb") as f:
            ldmbridge.LDMProductFactory(
                ingest, stdin=f.fileno(), reactor=reactor
            )

        self.runReactor(reactor)
        assert ingest.hits == 3


globals().update(StdioFilesTests.makeTestCaseClasses())
