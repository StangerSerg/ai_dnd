"""Каталог иконок предметов (подэтап 7): фиксированный allowlist, как у аватаров.

``Item.icon`` ссылается только на id из этого каталога; допустимость проверяет
``scenarios.service.find_scenario_issues`` (код ``unknown_icon``). Сами SVG-файлы —
статические ассеты frontend (``frontend/public/images/items/<id>.svg``); загрузок
пользовательских картинок нет. Атрибуция едина для всего каталога.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ItemIcon:
    id: str
    name_ru: str
    name_en: str


ICON_ATTRIBUTION = "собственные SVG-иконки проекта"

ITEM_ICONS: tuple[ItemIcon, ...] = (
    ItemIcon("sword", "Меч", "Sword"),
    ItemIcon("axe", "Топор", "Axe"),
    ItemIcon("bow", "Лук", "Bow"),
    ItemIcon("armor", "Доспех", "Armor"),
    ItemIcon("shield", "Щит", "Shield"),
    ItemIcon("potion", "Зелье", "Potion"),
    ItemIcon("scroll", "Свиток", "Scroll"),
    ItemIcon("ring", "Кольцо", "Ring"),
    ItemIcon("amulet", "Амулет", "Amulet"),
    ItemIcon("cloak", "Плащ", "Cloak"),
    ItemIcon("ammo", "Боеприпасы", "Ammunition"),
    ItemIcon("key", "Ключ", "Key"),
)

ITEM_ICON_IDS: frozenset[str] = frozenset(icon.id for icon in ITEM_ICONS)
