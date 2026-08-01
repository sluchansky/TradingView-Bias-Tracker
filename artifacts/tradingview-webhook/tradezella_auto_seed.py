"""Auto-seed review extraction from TradeZella CSV fields — PURE module.

Converts the free-text fields TradeZella already stores (mistake, notes,
setup, outcome, R-multiple, MFE/MAE) into structured journal_reviews data
so imported trades immediately count in coaching without manual click-through.

All logic is heuristic and fail-open.  Users can override any auto-filled
field via the review modal.  Manual reviews (review_status != 'UNREVIEWED')
are NEVER overwritten by this module.

Public API:
    auto_seed_review(trade: dict) -> dict
"""

# ---------------------------------------------------------------------------
# Keyword → tag maps
# ---------------------------------------------------------------------------

# Each entry is (phrase_in_lowercase, canonical_tag).
# Phrases are matched against the combined lowercased mistake + notes text.
# Longer phrases listed first so they match before their component words.
_MISTAKE_TAG_MAP = [
    ("without a stop",      "no_stop_loss"),
    ("without a plan",      "no_plan"),
    ("against the plan",    "no_plan"),
    ("no stop loss",        "no_stop_loss"),
    ("no stop",             "no_stop_loss"),
    ("no plan",             "no_plan"),
    ("moved my stop",       "moved_stop"),
    ("moved stop",          "moved_stop"),
    ("took profit early",   "cut_winner_early"),
    ("closed early",        "cut_winner_early"),
    ("cut early",           "cut_winner_early"),
    ("held too long",       "held_too_long"),
    ("let it run",          "held_too_long"),
    ("counter-trend",       "counter_trend"),
    ("counter trend",       "counter_trend"),
    ("against trend",       "counter_trend"),
    ("over-trading",        "overtrading"),
    ("overtrading",         "overtrading"),
    ("overtrade",           "overtrading"),
    ("over-sized",          "oversized"),
    ("oversized",           "oversized"),
    ("oversize",            "oversized"),
    ("entered late",        "late_entry"),
    ("late entry",          "late_entry"),
    ("entered early",       "early_entry"),
    ("early entry",         "early_entry"),
    ("impulse trade",       "impulsive"),
    ("impulsive",           "impulsive"),
    ("impulse",             "impulsive"),
    ("revenge",             "revenge_trade"),
    ("fomo",                "fomo"),
    ("chasing",             "chasing"),
    ("chased",              "chasing"),
    ("hesitat",             "hesitation"),   # hesitated / hesitation
    ("tilt",                "tilt"),
    ("fear",                "fear"),
    ("greed",               "greed"),
    ("impatient",           "impatient"),
    ("news",                "trading_news_event"),
]

# Each entry is (keyword_substring, canonical_tag, intensity_1_to_5).
_EMOTION_TAG_MAP = [
    ("overconfident",   "overconfidence",  4),
    ("tilt",            "tilt",            5),
    ("frustrat",        "frustration",     4),   # frustrated / frustration
    ("anxiety",         "anxiety",         4),
    ("anxious",         "anxiety",         3),
    ("greed",           "greed",           4),
    ("greedy",          "greed",           3),
    ("fear",            "fear",            4),
    ("scared",          "fear",            3),
    ("excited",         "excitement",      3),
    ("hesitat",         "hesitation",      3),
    ("confident",       "confidence",      3),
    ("calm",            "calm",            3),
    ("patient",         "patience",        3),
    ("disciplined",     "disciplined",     3),
]

_POSITIVE_KEYWORDS = (
    "followed plan",
    "followed my plan",
    "followed the plan",
    "stuck to plan",
    "stuck to my plan",
    "trusted the plan",
    "trusted my plan",
    "good risk management",
    "good execution",
    "clean setup",
    "textbook",
    "perfect entry",
    "great entry",
    "good entry",
    "patient",
    "disciplined",
)

# Phrases that indicate the plan WAS followed (checked in order; first match wins).
_FOLLOWED_YES = (
    "followed plan", "followed my plan", "followed the plan",
    "stuck to plan", "stuck to my plan",
    "trusted the plan", "trusted my plan",
    "textbook trade", "executed perfectly",
    "good trade execution",
)

# Phrases that indicate the plan was NOT followed.
_FOLLOWED_NO = (
    "no plan", "without a plan", "against the plan", "off my plan",
    "didn't follow", "did not follow", "deviated", "changed plan",
    "changed my plan", "impulsive", "impulse trade", "unplanned",
    "broke rules", "broke my rules", "rule violation", "off plan",
)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def auto_seed_review(trade: dict) -> dict:
    """Extract structured review data from a raw TradeZella trade dict.

    Returns a dict matching journal_reviews column names.

    review_status is set to:
      'REVIEWED'   — when at least one meaningful field could be extracted
      'UNREVIEWED' — when the trade has no text / outcome to auto-grade

    All heuristics are fail-open: ambiguous cases produce None rather than
    a wrong value.  Users can override any field via the review modal.
    This function never raises.
    """
    from datetime import datetime, timezone as _tz  # local import — no app dependency

    mistake = (trade.get("mistake") or "").strip()
    notes   = (trade.get("notes")   or "").strip()
    outcome = (trade.get("outcome") or "").lower().strip()
    r_mult  = trade.get("r_multiple")
    mfe     = trade.get("mfe")
    mae     = trade.get("mae")

    combined = (mistake + " " + notes).lower()

    # ── Mistake tags ──────────────────────────────────────────────────────────
    mistake_tags: list = []
    for phrase, tag in _MISTAKE_TAG_MAP:
        if phrase in combined and tag not in mistake_tags:
            mistake_tags.append(tag)

    # ── Positive tags ─────────────────────────────────────────────────────────
    positive_tags: list = []
    for kw in _POSITIVE_KEYWORDS:
        if kw in combined:
            norm = kw.replace(" ", "_")
            if norm not in positive_tags:
                positive_tags.append(norm)

    # ── Emotion tags ──────────────────────────────────────────────────────────
    emotion_tags: list = []
    seen_em: set = set()
    for kw, tag, intensity in _EMOTION_TAG_MAP:
        if kw in combined and tag not in seen_em:
            emotion_tags.append({"tag": tag, "intensity": intensity})
            seen_em.add(tag)

    # ── Followed plan ─────────────────────────────────────────────────────────
    followed_plan = "NOT_APPLICABLE"
    for phrase in _FOLLOWED_YES:
        if phrase in combined:
            followed_plan = "YES"
            break
    if followed_plan == "NOT_APPLICABLE":
        for phrase in _FOLLOWED_NO:
            if phrase in combined:
                followed_plan = "NO"
                break

    # ── Overall quality  (outcome × R-multiple) ───────────────────────────────
    # 5 = great trade, 3 = neutral / scratch / disciplined loss, 1 = stop blown
    try:
        r_mult = float(r_mult) if r_mult is not None else None
    except (TypeError, ValueError):
        r_mult = None

    overall_quality = None
    if outcome == "win":
        if r_mult is not None:
            if r_mult >= 2.0:
                overall_quality = 5
            elif r_mult >= 1.0:
                overall_quality = 4
            else:
                overall_quality = 3   # small winner — acceptable
        else:
            overall_quality = 4       # win but no R data
    elif outcome == "loss":
        if r_mult is not None:
            if r_mult > -1.0:
                overall_quality = 3   # sub-1R clean loss — disciplined
            elif r_mult >= -1.5:
                overall_quality = 2
            else:
                overall_quality = 1   # overrun stop
        else:
            overall_quality = 2       # loss but no R data
    elif outcome == "scratch":
        overall_quality = 3

    # ── Discipline quality  (mistake count × followed_plan) ───────────────────
    # 5 = no mistakes + followed plan, 1 = many mistakes or plan ignored
    discipline_quality = None
    n_mistakes = len(mistake_tags)
    if (n_mistakes > 0
            or followed_plan in ("YES", "NO")
            or outcome in ("win", "loss", "scratch")):
        base = max(1, 5 - n_mistakes)           # 0→5, 1→4, 2→3, 3→2, 4+→1
        if followed_plan == "YES":
            base = min(5, base + 1)
        elif followed_plan == "NO":
            base = max(1, base - 1)
        discipline_quality = max(1, min(5, base))

    # ── Execution quality  (MFE/MAE adverse-excursion share) ──────────────────
    # Low adverse share → entered early → good execution
    execution_quality = None
    try:
        if mfe is not None and mae is not None:
            mfe_f = float(mfe)
            mae_f = abs(float(mae))
            total = mfe_f + mae_f
            if total > 0:
                mae_share = mae_f / total
                if mae_share < 0.20:
                    execution_quality = 5
                elif mae_share < 0.35:
                    execution_quality = 4
                elif mae_share < 0.50:
                    execution_quality = 3
                elif mae_share < 0.65:
                    execution_quality = 2
                else:
                    execution_quality = 1
    except (TypeError, ValueError):
        pass

    # ── Notes ────────────────────────────────────────────────────────────────
    pre_trade_notes   = notes if notes else None
    post_trade_review = (
        f"[Auto-seeded from TradeZella] Mistake noted: {mistake}"
        if mistake else None
    )

    # ── Review status ─────────────────────────────────────────────────────────
    # Set REVIEWED when at least one meaningful field was extracted.
    has_data = bool(
        mistake_tags
        or emotion_tags
        or positive_tags
        or (followed_plan != "NOT_APPLICABLE")
        or (overall_quality is not None)
    )
    review_status = "REVIEWED" if has_data else "UNREVIEWED"
    reviewed_at   = datetime.now(_tz.utc).isoformat() if has_data else None

    return {
        "review_status":      review_status,
        "followed_plan":      followed_plan,
        "mistake_tags":       mistake_tags,
        "positive_tags":      positive_tags,
        "emotion_tags":       emotion_tags,
        "pre_trade_notes":    pre_trade_notes,
        "post_trade_review":  post_trade_review,
        "discipline_quality": discipline_quality,
        "overall_quality":    overall_quality,
        "execution_quality":  execution_quality,
        "reviewed_at":        reviewed_at,
    }
