"""The mood vocabulary: names, UI sections and the text prompt that describes each mood to CLAP.

Sections keep a long list usable: basic moods apply to anything; the rock/punk/metal and rave/electronic sections
add the finer shades those genres need (a punk track is rarely just "energetic": it is rebellious, cheeky, driving).
Names the user adds himself go to "Свои".
"""

from __future__ import annotations

BASIC = "Базовые"
ROCK = "Рок · панк · метал"
RAVE = "Рейв · электроника"
OTHER = "Другое"
CUSTOM = "Свои"
GROUPS = [BASIC, ROCK, RAVE, OTHER, CUSTOM]

# (name, section, CLAP prompt)
MOODS: list[tuple[str, str, str]] = [
    # bright and fast
    ("Весёлое", BASIC, "happy cheerful upbeat music"),
    ("Мажорное", BASIC, "bright major-key music, sunny, positive and bouncy"),
    ("Энергичное", BASIC, "energetic driving music with a fast tempo"),
    ("Быстрое", BASIC, "very fast tempo music, rapid and racing"),
    ("Танцевальное", RAVE, "danceable club electronic music with a steady four-on-the-floor beat"),
    ("Ритмичное", BASIC, "rhythmic groovy music driven by a strong repetitive beat and bass line"),
    ("Победное", ROCK, "triumphant anthemic stadium rock, uplifting and powerful"),
    ("Угарное", RAVE, "wild crazy funny party music, rowdy, joking and hard-hitting"),
    ("Ироничное", OTHER, "ironic funny comedic satirical song"),
    # warm and quiet
    ("Меланхоличное", BASIC, "melancholic wistful bittersweet tender music"),
    ("Мелодичное", ROCK, "melodic emotional rock with big catchy vocal hooks"),
    ("Ностальгия", BASIC, "nostalgic wistful music"),
    ("Спокойное", BASIC, "calm relaxing peaceful music"),
    ("Медленное", BASIC, "slow tempo music, unhurried and drawn-out"),
    ("Фоновое", BASIC, "quiet ambient background music"),
    # sad, deep, dark
    ("Грустное", BASIC, "sad melancholic music"),
    ("Глубокое", OTHER, "thoughtful introspective music with deep meaningful lyrics, philosophical and serious"),
    ("Саспенс", OTHER, "suspenseful tense anxious music, building tension"),
    ("Мрачное", ROCK, "dark gloomy heavy music, ominous and gothic atmosphere"),
    ("Таинственное", OTHER, "mysterious enigmatic mystical music, eerie and magical atmosphere"),
    ("Гипнотическое", RAVE, "hypnotic psychedelic trippy repetitive trance-inducing groove, spacey and swirling"),
    ("Ночное", RAVE, "nocturnal night music, from neon night drives to soft late-night calm for falling asleep"),
    # hard
    ("Злое", BASIC, "aggressive angry heavy music"),
    ("Бунтарское", ROCK, "rebellious defiant cheeky punk rock, protest, attitude and mischief"),
    ("Тяжёлое", ROCK, "heavy brutal metal with distorted down-tuned guitars and screaming"),
    ("Оркестровое", ROCK, "orchestral epic symphonic music, cinematic strings and choirs, grand and heroic"),
    ("Рейв", RAVE, "rave music with a hard pounding beat, breakbeats, acid synths and relentless dancefloor energy"),
]

# name -> (light colour, simple glyph) for the mood buttons
STYLE: dict[str, tuple[str, str]] = {
    "Весёлое": ("#FFD96B", "☀"), "Грустное": ("#8FB4E8", "☂"), "Энергичное": ("#FFA45C", "⚡"),
    "Спокойное": ("#9EDBC4", "☾"), "Меланхоличное": ("#C7A9E0", "❦"), "Злое": ("#F28B82", "✖"),
    "Ностальгия": ("#D9BE95", "↺"), "Фоновое": ("#B8C4CF", "≈"), "Ритмичное": ("#7FD6E0", "♩"), "Мажорное": ("#FFE08A", "♯"), "Быстрое": ("#FFB48A", "»"), "Медленное": ("#A8D4C0", "«"), "Таинственное": ("#B9A5E0", "✧"),
    "Бунтарское": ("#F28B82", "✊"), 
    "Мрачное": ("#A9A3C4", "●"), "Оркестровое": ("#E0B96B", "♛"), "Тяжёлое": ("#B0B5BD", "▼"),
    "Мелодичное": ("#A5D0F0", "♪"), "Победное": ("#F3D36B", "✔"),
    "Танцевальное": ("#F59BD0", "♫"), "Гипнотическое": ("#88C9D8", "◎"),
    "Рейв": ("#8BE3A0", "▮"), "Угарное": ("#FF9AA2", "!"),
    "Ночное": ("#8EA2E8", "☽"), "Ироничное": ("#D4D98A", "~"), "Саспенс": ("#E8B08A", "?"),
    "Глубокое": ("#9AB8D8", "◈"),
}


def style_of(name: str) -> tuple[str, str]:
    return STYLE.get(name, ("#C8CDD3", "•"))

# vocabulary clean-ups applied to existing catalogues once: old name -> the mood it was folded into / dropped moods
MOOD_RENAMES = {"Агрессивное": "Злое", "Дерзкое": "Бунтарское", "Драйвовое": "Энергичное", "Психоделичное": "Гипнотическое",
                "Шуточное": "Угарное", "Романтичное": "Меланхоличное", "Тревожное": "Саспенс", "Эпическое": "Оркестровое", "Для сна": "Ночное"}
MOOD_DROPPED = ["Эйфорическое", "Для спорта"]

DEFAULT_MOODS = [m[0] for m in MOODS]
PROMPTS = {name: prompt for name, _, prompt in MOODS}
_GROUP = {name: grp for name, grp, _ in MOODS}


def group_of(name: str) -> str:
    return _GROUP.get(name, CUSTOM)


def ordered(names: list[str]) -> list[str]:
    """Names in vocabulary order, user-defined ones last (stable)."""
    rank = {n: i for i, n in enumerate(DEFAULT_MOODS)}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))


def grouped(names: list[str]) -> list[tuple[str, list[str]]]:
    """[(section, [names])] in section order, empty sections dropped."""
    out = []
    for g in GROUPS:
        items = [n for n in ordered(names) if group_of(n) == g]
        if items:
            out.append((g, items))
    return out
