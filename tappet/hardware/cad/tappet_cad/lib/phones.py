"""Phone mock-ups (Android head + target iPhones).  Frame: phone lying screen-up, centred at the
origin in x/y, back face at z=0, long axis along y."""
from __future__ import annotations

from .util import rbox

DEVICES = {
    # name: (body_w, body_l, body_t, corner_r)
    "iphone_15_61": (71.6, 147.6, 7.8, 9.0),       # iPhone 15 / 15 Pro / 16 (6.1")
    "iphone_15_plus_67": (77.8, 160.9, 7.8, 10.0), # iPhone 15/16 Plus (6.7"); 15 Pro Max 76.7x159.9
    "iphone_se": (67.3, 138.4, 7.3, 6.0),          # iPhone SE 2nd/3rd gen
    "android_head": (75.0, 160.0, 9.0, 9.0),       # representative Android head phone
}


def phone_body(name: str):
    w, l, t, r = DEVICES[name]
    return rbox(-w / 2, w / 2, -l / 2, l / 2, 0, t - 0.3, r)


def phone_screen(name: str):
    w, l, t, r = DEVICES[name]
    return rbox(-w / 2 + 1.6, w / 2 - 1.6, -l / 2 + 1.6, l / 2 - 1.6, t - 0.3, t, max(r - 1.6, 1.0))
