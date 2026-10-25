"""BeamMP sharing lifecycle, isolated from terrain generation and streaming."""
import os
import secrets
import peerwork as peerworkmod


def share_port(asked=None, env=None):
    """The port our BeamMP group service listens on: BNGE_SHARE_PORT on this machine, else the BeamMP server's (its
    plugin's SHARE_PORT, through the mod's "share"), else peerwork.SHARE_PORT; an unusable one is skipped."""
    env = os.environ.get("BNGE_SHARE_PORT") if env is None else env
    for p in (env, asked):
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if 1024 <= p <= 65535:
            return p
    return peerworkmod.SHARE_PORT


class SharingServerMixin:
    def share_start(self, port=None):
        """Compute sharing in a BeamMP group: our GroupHelper listening (started once) on port (the BeamMP server's
        setting, its plugin's SHARE_PORT; BNGE_SHARE_PORT on this machine wins; else peerwork.SHARE_PORT). -> {port,
        code} (port None: not sharing)."""
        if not hasattr(self.far_pool, "set_group"):
            return {"port": None, "code": peerworkmod.code_id()}
        self.far_pool.sharing = True        # every tile built is kept from now on (a player arriving gets ours)
        with self.share_lock:
            if self.group_helper is None:
                self.group_helper = peerworkmod.GroupHelper(self.far_pool, self.anchor_id_for,
                                                            os.path.join(self.cache_dir, "peer-out"))
                self.group_helper.start(share_port(port))
        return {"port": self.group_helper.port, "code": peerworkmod.code_id(), "weight": peerworkmod.share_weight(),
                "token": self.far_pool.session_token}

    def share_group(self, msg):
        """The mod's "peers": {me, members: [{id, host, port, code}]}: the players on our anchor with our code."""
        if not hasattr(self.far_pool, "set_group"):
            return
        code = peerworkmod.code_id()
        members, weights, tokens = [], {}, {}
        for m in msg.get("members") or []:
            try:
                if m.get("code") == code and m.get("host") and 0 < int(m.get("port")) < 65536:
                    members.append((str(m["id"]), str(m["host"]), int(m["port"])))
                    tokens[str(m["id"])] = m.get("token")
                    w = float(m.get("weight") or 1.0)
                    weights[str(m["id"])] = w if 0.1 <= w <= 1000 else 1.0
            except (TypeError, ValueError, AttributeError):
                continue
        rotated = self.far_pool.set_group(str(msg.get("me")), members, weights, tokens)
        return self.share_start() if rotated else None

    def anchor_id_for(self, lat, lon):
        """Our anchor id at (lat, lon) when we have one there (its graders are made already), else the helper's."""
        for a in list(self.anchors.values()):
            if float(a.lat) == lat and float(a.lon) == lon:
                return a.id
        return peerworkmod._anchor_id(lat, lon)

    def remote_slots(self):
        """Far tiles compute sharing's helpers build at once (0 without helpers)."""
        f = getattr(self.far_pool, "remote_slots", None)
        return f() if f is not None else 0

    def share_stop(self):
        """Revoke credentials and close the listener when the game leaves multiplayer."""
        if hasattr(self.far_pool, "set_group"):
            self.far_pool.set_group("", [])
            self.far_pool.sharing = False
            self.far_pool.session_token = secrets.token_hex(32)
        with self.share_lock:
            if self.group_helper is not None:
                self.group_helper.stop()
                self.group_helper = None

