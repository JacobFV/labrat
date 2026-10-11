import pytest

from tappet.client import BodyClient
from tappet.sim import start_sim

SPEED = 50.0


@pytest.fixture
def sim():
    srv = start_sim(port=0, speed=SPEED)
    yield srv
    srv.close()


@pytest.fixture
def body(sim):
    c = BodyClient.connect(sim.url, heartbeat_s=None)
    yield c
    c.close()
