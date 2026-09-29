"""Send-statistics cases shared by the Python and SQL validator tests (#284).

Both validators must agree on every case, so the rule has one definition in
family_delivery.send_stats and one in stewardship_family_smtp_result_v1.
"""

ADMITTED = [
    {},
    {
        "smtp_ms": 1,
        "total_ms": 999_999_999_999,
        "conn_reused": True,
        "token_refreshed": False,
        "transport": "per_message",
        "conn_end": "token_failed",
        "prev_helper_end": "key_change",
        "helper_id": "h0123456789ab",
    },
    # Numeric keys stay open: a new timing needs no schema change.
    {"a_new_timing_ms": 7},
    {f"k{index}_ms": index for index in range(48)},
]

REFUSED = {
    "address": {"who": "a@example.org"},
    "host": {"host": "smtp.gmail.com"},
    "uppercase_key": {"Upper": 1},
    "prose": {"transport": "two words"},
    "word_not_allowed": {"transport": "carrier_pigeon"},
    "word_for_other_key": {"conn_end": "batched"},
    "free_word_key": {"reason": "idle"},
    "helper_id_shape": {"helper_id": "h0123"},
    "nested_object": {"nested": {"a": 1}},
    "array": {"list": [1]},
    "null": {"none": None},
    "negative": {"x_ms": -1},
    "fraction": {"x_ms": 0.5},
    "too_large": {"x_ms": 1_000_000_000_000},
    # A number sent as text is still text: timings are numbers only.
    "numeric_string": {"smtp_ms": "12"},
    "overlong_key": {"x" * 41: 1},
    "overlong_value": {"transport": "b" * 40},
    "too_many_keys": {f"k{index}_ms": index for index in range(49)},
}
