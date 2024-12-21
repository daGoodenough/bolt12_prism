import os
import time

import pytest
from pyln.client import RpcError
from pyln.testing.fixtures import *  # noqa: F401,F403
from pyln.testing.utils import sync_blockheight, wait_for

plugin_path = os.path.join(os.path.dirname(__file__), "./bolt12-prism.py")
plugin_opt = {"plugin": plugin_path}


def experimental_offers_check(node_factory):
    l1 = node_factory.get_node()
    version = l1.rpc.getinfo()["version"]
    if version.startswith("v24.0"):
        return True
    else:
        return False


# spin up a network
def test_basic_test(node_factory):
    # Start five lightning nodes
    l1, l2, l3, l4, l5 = node_factory.get_nodes(5)

    # Connect the nodes in a prism layout.
    l1.rpc.connect(l2.info["id"], "localhost", l2.port)  # Alice -> Bob
    l2.rpc.connect(l3.info["id"], "localhost", l3.port)  # Bob -> Carol
    l2.rpc.connect(l4.info["id"], "localhost", l4.port)  # Bob -> Dave
    l2.rpc.connect(l5.info["id"], "localhost", l5.port)  # Bob -> Erin

    # Check the list of peers to confirm connection
    # Alice -> Bob
    assert len(l1.rpc.listpeers()["peers"]) == 1
    assert l1.rpc.listpeers()["peers"][0]["id"] == l2.info["id"]

    # Bob -> all others
    assert len(l2.rpc.listpeers()["peers"]) == 4
    # TODO we don't really know which index an id will be in; just search for existence?

    # try:
    assert l2.rpc.plugin_start(plugin_path), "Failed to start plugin"
    list_plugin = l2.rpc.plugin_list()
    our_plugin = [
        plugin
        for plugin in list_plugin["plugins"]
        if "bolt12-prism" in plugin["name"]
    ]
    assert len(our_plugin) > 0
    assert l2.rpc.prism_list()
    assert l2.rpc.prism_listbindings()
    assert l2.rpc.plugin_stop(plugin_path)


def test_general_prism(node_factory, bitcoind):
    opts = [{},{},{},{},{}]
    if experimental_offers_check(node_factory):
        opts[0]["experimental-offers"] = None
        opts[1]["experimental-offers"] = None
        opts[2]["experimental-offers"] = None
        opts[3]["experimental-offers"] = None
        opts[4]["experimental-offers"] = None

    l1, l2, l3, l4, l5 = node_factory.get_nodes(5, opts)
    nodes = [l1, l2, l3, l4, l5]

    # fund nodes, create channels, l2 is the prism
    #
    #          -> l3
    # l1 -> l2 -> l4
    #          -> l5
    #
    l1.fundwallet(10_000_000)
    l2.fundwallet(10_000_000)
    l1.rpc.fundchannel(l2.info["id"] + "@localhost:" + str(l2.port), 1_000_000)
    l2.rpc.fundchannel(l3.info["id"] + "@localhost:" + str(l3.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l4.info["id"] + "@localhost:" + str(l4.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l5.info["id"] + "@localhost:" + str(l5.port), 1_000_000)
    bitcoind.generate_block(6)
    sync_blockheight(bitcoind, nodes)

    l2.rpc.plugin_start(plugin_path)

    l3_offer = l3.rpc.offer("any", "Lead-Singer")
    l4_offer = l4.rpc.offer("any", "Drummer")
    l5_offer = l5.rpc.offer("any", "Guitarist")

    members_json = [
        {
            "description": "Lead-Singer",
            "destination": l3_offer["bolt12"],
            "split": 1.0,
        },
        {
            "description": "Drummer",
            "destination": l4_offer["bolt12"],
            "split": 1,
        },
        {
            "description": "Guitarist",
            "destination": l5_offer["bolt12"],
            "split": 1.0,
        },
    ]
    description = "prism1"

    prism = l2.rpc.call(
        "prism-create", {"members": members_json, "description": description}
    )

    prism1_id = prism["prism_id"]

    prism_ids = [prism["prism_id"] for prism in l2.rpc.call("prism-list")["prisms"]]
    assert prism1_id in prism_ids

    l2.rpc.call("prism-pay", {"prism_id": prism1_id, "amount_msat": 1_000_000})
    wait_for(
        lambda: l3.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 300_000
    )
    wait_for(
        lambda: l4.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 300_000
    )
    wait_for(
        lambda: l5.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 300_000
    )

    l2_offer = l2.rpc.offer("any", "Prism")
    l2.rpc.call(
        "prism-addbinding",
        {"offer_id": l2_offer["offer_id"], "prism_id": prism1_id},
    )
    binding = l2.rpc.call("prism-listbindings")["bolt12_prism_bindings"][0]
    assert binding["offer_id"] == l2_offer["offer_id"]
    assert binding["prism_id"] == prism1_id

    invoice = l1.rpc.fetchinvoice(l2_offer["bolt12"], 1_000_000)
    l1.rpc.pay(invoice["invoice"])
    wait_for(
        lambda: l3.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )
    wait_for(
        lambda: l4.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )
    wait_for(
        lambda: l5.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )

    l2.rpc.call("prism-deletebinding", {"offer_id": l2_offer["offer_id"]})
    assert len(l2.rpc.call("prism-listbindings")["bolt12_prism_bindings"]) == 0

    l2.rpc.call("prism-delete", {"prism_id": prism1_id})
    assert prism1_id not in [prism["prism_id"] for prism in l2.rpc.call("prism-list")["prisms"]] 


def test_splits(node_factory, bitcoind):
    opts = [{},{},{},{}]
    if experimental_offers_check(node_factory):
        opts[0]["experimental-offers"] = None
        opts[1]["experimental-offers"] = None
        opts[2]["experimental-offers"] = None
        opts[3]["experimental-offers"] = None

    l1, l2, l3, l4 = node_factory.get_nodes(4, opts)
    nodes = [l1, l2, l3, l4]

    # fund nodes, create channels, l1 is the prism
    #
    #    -> l2
    # l1 -> l3
    #    -> l4
    #
    l1.fundwallet(10_000_000)
    l1.rpc.fundchannel(l2.info["id"] + "@localhost:" + str(l2.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l1.rpc.fundchannel(l3.info["id"] + "@localhost:" + str(l3.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l1.rpc.fundchannel(l4.info["id"] + "@localhost:" + str(l4.port), 1_000_000)
    bitcoind.generate_block(6)
    sync_blockheight(bitcoind, nodes)

    l1.rpc.plugin_start(plugin_path)

    l2_offer = l2.rpc.offer("any", "CEO")
    l3_offer = l3.rpc.offer("any", "CTO")
    l4_offer = l4.rpc.offer("any", "Janitor")

    prism1_description = "prism1"

    members_json = [
        {
            "description": "CEO",
            "destination": l2_offer["bolt12"],
            "split": 5,
        },
        {
            "description": "CTO",
            "destination": l3_offer["bolt12"],
            "split": 3.0,
        },
        {
            "description": "Janitor",
            "destination": l4_offer["bolt12"],
            "split": 1.0,
        },
    ]

    prism = l1.rpc.call(
        "prism-create", {"members": members_json, "description": prism1_description}
    )
    prism1_id = prism["prism_id"]

    l1.rpc.call("prism-pay", {"prism_id": prism1_id, "amount_msat": 1_000_000})
    wait_for(
        lambda: l2.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 555_000
    )
    wait_for(
        lambda: l3.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 333_000
    )
    wait_for(
        lambda: l4.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 111_000
    )


def test_payment_threshold(node_factory, bitcoind):
    opts = [{},{},{},{},{}]
    if experimental_offers_check(node_factory):
        opts[0]["experimental-offers"] = None
        opts[1]["experimental-offers"] = None
        opts[2]["experimental-offers"] = None
        opts[3]["experimental-offers"] = None
        opts[4]["experimental-offers"] = None

    l1, l2, l3, l4, l5 = node_factory.get_nodes(5, opts)
    nodes = [l1, l2, l3, l4, l5]

    # fund nodes, create channels, l2 is the prism
    #
    #          -> l3
    # l1 -> l2 -> l4
    #          -> l5
    #
    l1.fundwallet(10_000_000)
    l2.fundwallet(10_000_000)
    l1.rpc.fundchannel(l2.info["id"] + "@localhost:" + str(l2.port), 1_000_000)
    l2.rpc.fundchannel(l3.info["id"] + "@localhost:" + str(l3.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l4.info["id"] + "@localhost:" + str(l4.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l5.info["id"] + "@localhost:" + str(l5.port), 1_000_000)
    bitcoind.generate_block(6)
    sync_blockheight(bitcoind, nodes)

    l2.rpc.plugin_start(plugin_path)

    l3_offer = l3.rpc.offer("any", "Lead-Singer")
    l4_offer = l4.rpc.offer("any", "Drummer")
    l5_offer = l5.rpc.offer("any", "Guitarist")

    members_json = [
        {
            "description": "Lead-Singer",
            "destination": l3_offer["bolt12"],
            "split": 1.0,
            "payout_threshold_msat": 500000,
        },
        {
            "description": "Drummer",
            "destination": l4_offer["bolt12"],
            "split": 1.0,
        },
        {
            "description": "Guitarist",
            "destination": l5_offer["bolt12"],
            "split": 1,
        },
    ]
    prism1_description = "prism1"

    prism = l2.rpc.call(
        "prism-create", {"members": members_json, "description": prism1_description}
    )
    prism1_id = prism["prism_id"]

    l2_offer = l2.rpc.offer("any", "Prism")
    l2.rpc.call(
        "prism-addbinding",
        {"offer_id": l2_offer["offer_id"], "prism_id": prism1_id},
    )

    invoice = l1.rpc.fetchinvoice(l2_offer["bolt12"], 1_000_000)
    l1.rpc.pay(invoice["invoice"])
    wait_for(
        lambda: l4.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 300_000
    )
    wait_for(
        lambda: l5.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 300_000
    )
    # hold, CI can be slow
    time.sleep(5)
    assert l3.rpc.listpeerchannels()["channels"][0]["to_us_msat"] == 0

    invoice = l1.rpc.fetchinvoice(l2_offer["bolt12"], 1_000_000)
    l1.rpc.pay(invoice["invoice"])
    wait_for(
        lambda: l3.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )
    wait_for(
        lambda: l4.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )
    wait_for(
        lambda: l5.rpc.listpeerchannels()["channels"][0]["to_us_msat"] > 600_000
    )

def test_update_outlay(node_factory, bitcoind):
    opts = [{},{},{},{},{}]
    if experimental_offers_check(node_factory):
        opts[0]["experimental-offers"] = None
        opts[1]["experimental-offers"] = None
        opts[2]["experimental-offers"] = None
        opts[3]["experimental-offers"] = None
        opts[4]["experimental-offers"] = None

    l1, l2, l3, l4, l5 = node_factory.get_nodes(5, opts)
    nodes = [l1, l2, l3, l4, l5]

    # fund nodes, create channels, l2 is the prism
    #
    #          -> l3
    # l1 -> l2 -> l4
    #          -> l5
    #
    l1.fundwallet(10_000_000)
    l2.fundwallet(10_000_000)
    l1.rpc.fundchannel(l2.info["id"] + "@localhost:" + str(l2.port), 1_000_000)
    l2.rpc.fundchannel(l3.info["id"] + "@localhost:" + str(l3.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l4.info["id"] + "@localhost:" + str(l4.port), 1_000_000)
    bitcoind.generate_block(1)
    sync_blockheight(bitcoind, nodes)
    l2.rpc.fundchannel(l5.info["id"] + "@localhost:" + str(l5.port), 1_000_000)
    bitcoind.generate_block(6)
    sync_blockheight(bitcoind, nodes)

    l2.rpc.plugin_start(plugin_path)

    l3_offer = l3.rpc.offer("any", "Lead-Singer")
    l4_offer = l4.rpc.offer("any", "Drummer")
    l5_offer = l5.rpc.offer("any", "Guitarist")

    members_json = [
        {
            "description": "Lead-Singer",
            "destination": l3_offer["bolt12"],
            "split": 1,
            "payout_threshold_msat": 500000,
        },
        {
            "description": "Drummer",
            "destination": l4_offer["bolt12"],
            "split": 1.0,
        },
        {
            "description": "Guitarist",
            "destination": l5_offer["bolt12"],
            "split": 1.0,
        },
    ]
    prism1_description = "prism1"

    prism = l2.rpc.call(
        "prism-create", {"members": members_json, "description": prism1_description}
    )
    prism1_id = prism["prism_id"]

    # generate offer and bind to it
    l2_offer = l2.rpc.offer("any", "Prism")
    binding = l2.rpc.call(
        "prism-addbinding",
        {"offer_id": l2_offer["offer_id"], "prism_id": prism1_id},
    )

    lead_singer_id = [member["member_id"] for member in binding["prism_members"] if member["description"] == "Lead-Singer"][0]
    assert lead_singer_id is not None, "Lead-Singer not found in prism"

    new_outlay_msat = 1000000

    # just updates a single member's outlay
    updated_binding = l2.rpc.call("prism-setoutlay", {"offer_id": l2_offer["offer_id"], "member_id": lead_singer_id, "new_outlay_msat": new_outlay_msat})["bolt12_prism_bindings"]

    print(f"updated_binding: {updated_binding}")

    assert updated_binding["member_outlays"][0]["outlay_msat"] == new_outlay_msat, "Outlay not updated correctly"

    sanity_check = l2.rpc.call("prism-listbindings", {"offer_id": l2_offer["offer_id"]})["bolt12_prism_bindings"]

    assert sanity_check["member_outlays"][0]["outlay_msat"] == new_outlay_msat, "Outlay not updated in DB correctly"
