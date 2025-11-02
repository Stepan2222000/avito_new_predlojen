from urllib.parse import urlencode

# Пути категорий на Avito
CATEGORY_PATHS = {
    "avtomobili": "/avtomobili",
    "mototsikly": "/mototsikly_i_mototehnika/mototsikly",
    "kvadrotsikly": "/mototsikly_i_mototehnika/kvadrotsikly",
    "snegohody": "/mototsikly_i_mototehnika/snegohody",
    "katera_i_yahty": "/vodnyy_transport/katera_i_yahty",
    "gidrotsikly": "/vodnyy_transport/gidrotsikly"
}

# Родовые слова для параметра enrich_q (добавляются в КОНЕЦ строки q)
# avtomobili не включается - родовое слово не добавляется
ENRICH_Q_WORDS = {
    "mototsikly": "мотоцикл",
    "snegohody": "снегоход",
    "gidrotsikly": "гидроцикл",
    "katera_i_yahty": "катер",
    "kvadrotsikly": "квадроцикл"
}


def build_url(group_dict: dict, brand: str = None, model: str = None) -> str:
    """
    Построение одного URL для парсинга на основе group_dict и опциональных brand/model.

    Args:
        group_dict: Словарь группы из groups.json
        brand: Бренд для поиска (опционально)
        model: Модель для поиска (опционально)

    Returns:
        Полный URL для парсинга каталога Avito
    """
    # Определение региона (all_russia ? '/all' : region_slug)
    region = "all" if group_dict.get("all_russia", False) else group_dict["region_slug"]

    # Получение пути категории
    category = group_dict["category"]
    category_path = CATEGORY_PATHS[category]

    # Формирование параметра q (БЕЗ изменения регистра)
    q = None
    if brand and model:
        q = f"{brand} {model}"
    elif brand:
        q = brand
    elif model:
        q = model

    # Применение enrich_q (добавление родового слова В КОНЕЦ)
    if q and group_dict.get("enrich_q", False) and category in ENRICH_Q_WORDS:
        q = f"{q} {ENRICH_Q_WORDS[category]}"

    # Формирование query параметров
    params = {
        "cd": "1",
        "radius": "0",
        "searchRadius": "0",
        "localPriority": "0",
        "s": "104"
    }

    # Добавление q только если он существует
    if q:
        params["q"] = q

    query_string = urlencode(params)

    # Возврат полного URL
    return f"https://www.avito.ru/{region}{category_path}?{query_string}"


def generate_task_urls(group_dict: dict) -> list[str]:
    """
    Генерация всех URL задач для группы (Инвариант 2).

    Args:
        group_dict: Словарь группы из groups.json

    Returns:
        Список URL для всех комбинаций brands/models

    Examples:
        brands=["audi", "bmw"], models={"audi": ["a6", "q7"], "bmw": ["x5"]} → 5 URLs
        brands=[], models={} → 1 URL (вся категория без q)
        brands=["audi"], models={"audi": []} → 1 URL (q=audi)
    """
    brands = group_dict.get("brands", [])
    models = group_dict.get("models", {})

    # Если нет брендов и моделей - вся категория без параметра q
    if not brands and not models:
        return [build_url(group_dict)]

    urls = []

    # Для каждого бренда создаем URL
    for brand in brands:
        # URL для бренда (q=brand)
        urls.append(build_url(group_dict, brand=brand))

        # Если есть модели для этого бренда
        if brand in models and models[brand]:
            # URL для каждой модели (q=brand model)
            for model in models[brand]:
                urls.append(build_url(group_dict, brand=brand, model=model))

        # Если models[brand] = [] (пустой список), создается только URL для бренда (уже добавлен выше)

    return urls
