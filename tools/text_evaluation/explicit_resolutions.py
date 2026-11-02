"""原文中无歧义的日期与行政区提及；只读正文、预测实体及公开参考日期。"""

from datetime import date, timedelta
import re


def explicit_resolutions(text, graph, options):
    result = {}
    def add(mention, dimension, granularity, value):
        levels = options.time_granularities if dimension == 'time' else options.space_granularities
        if granularity in levels and mention in text:
            key = (mention, dimension, granularity)
            result[key] = dict(mention=mention, dimension=dimension, granularity=granularity, value=value)
    def add_date(mention, value):
        for level, normalized in [('year', value.strftime('%Y')), ('month', value.strftime('%Y-%m')), ('day', value.isoformat())]:
            add(mention, 'time', level, normalized)
    for match in re.finditer(r'(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})[日号]', text):
        try:
            value = date(*(int(item) for item in match.groups()))
        except ValueError:
            continue
        add_date(match.group(), value)
    if options.reference_date:
        reference = date.fromisoformat(options.reference_date)
        for mention, days in [('今天', 0), ('昨天', -1), ('昨日', -1), ('前天', -2), ('明天', 1), ('后天', 2)]:
            if mention in text:
                add_date(mention, reference + timedelta(days=days))
    for entity in graph.get('entities', []):
        if entity.get('type') != '地理空间' or entity.get('name') not in text:
            continue
        name = entity['name']
        if '中国' in name:
            add('中国', 'space', 'country', '中国')
        if name == '中华人民共和国':
            add(name, 'space', 'country', name)
        for match in re.finditer(r'[^省市区县国，。\s]{1,12}?(?:自治区|特别行政区|省|市|区|县)', name):
            mention = match.group()
            level = 'province' if mention.endswith(('省', '自治区', '特别行政区')) else 'city' if mention.endswith('市') else 'district'
            add(mention, 'space', level, mention)
    return result
