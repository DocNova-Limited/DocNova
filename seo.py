"""Product pages Google can read (/p/<id>), the Google product feed and the sitemap.

Everything is built from the same files the shop page uses (dist/app.js and dist/photos.js) and from the
stock records, so names, prices, pictures and availability can never drift apart from the website.
"""
import html, json, re, time
from xml.sax.saxutils import escape as x
import shop, inventory
from shop import ROOT

SITE = 'https://docnova.co.uk'
FEED_DEVICES = False  # medical devices stay out of the Google feed until the owner confirms the paperwork
MATERIAL = '72% polyester, 21% rayon, 7% spandex'
_cache = {'stamp': None, 'data': None}
_stock = {'at': 0, 'left': {}}

def _extra():
    """Picture groups, main-picture choices and device wording, read from the storefront files."""
    app, pho = ROOT / 'dist' / 'app.js', ROOT / 'dist' / 'photos.js'
    stamp = (app.stat().st_mtime, pho.stat().st_mtime)
    if _cache['stamp'] == stamp:
        return _cache['data']
    src = app.read_text(encoding='utf-8')
    photos = json.loads(re.search(r'const photos=(\{.*\});', pho.read_text(encoding='utf-8'), flags=re.S).group(1))
    for group in ('bp', 'thermometer'):
        names = re.search(r"photos\." + group + r"=\[(.*?)\]\.map", src, flags=re.S).group(1)
        photos[group] = ['assets/devices/' + n for n in re.findall(r"'([^']+)'", names)]
    start = src.index('const products=[')
    groups = {}
    for obj in re.findall(r"\{id:'[^\n]*?\}", src[start:src.index('];', start)]):
        groups[(shop._field(obj, 'fit'), shop._field(obj, 'color'))] = shop._field(obj, 'group')
    leads = json.loads(re.search(r'leads=(\{.*?\}\}),imageOf', src, flags=re.S).group(1))
    devices = json.loads(re.search(r'var deviceInfo=(\{.*?\});\n', src, flags=re.S).group(1))
    data = {'photos': photos, 'groups': groups, 'leads': leads, 'devices': devices}
    _cache.update(stamp=stamp, data=data)
    return data

def products():
    return shop.catalogue()

def group_of(p):
    if p['device']:
        return 'bp' if p['id'] == 'blood-pressure-monitor' else 'thermometer'
    return _extra()['groups'].get((p['fit'], p['color']))

def images(p):
    """Main picture first (the same one the shop card shows), then the rest of the gallery."""
    e = _extra()
    gallery = list(e['photos'].get(group_of(p)) or [])
    if p['device']:  # photographs only: the drawn information cards carry text, which Google does not accept
        gallery = [g for g in gallery if 'studio' in g or 'photo' in g]
    lead = None
    for name in (e['leads'].get(str(group_of(p))) or {}).get(p['category'], []):
        lead = next((g for g in gallery if '/' + name + '.' in g), None)
        if lead:
            break
    lead = lead or (gallery[0] if gallery else 'assets/docnova-share.jpg')
    return [lead] + [g for g in gallery if g != lead]

def url(path):
    return SITE + '/' + path.lstrip('/')

def piece(p):
    return {'Tops': 'Top', 'Pants': 'Trousers', 'Sets': 'Set'}.get(p['category'], '')

def title(p):
    if p['device']:
        return p['name']
    return f"DocNova {p['fit']}’s Premium Scrub {piece(p)} – {p['color']}"

def description(p):
    """Plain wording taken from the product page; nothing is claimed here that the page does not say."""
    if p['device']:
        d = _extra()['devices'].get(p['id']) or {}
        return (d.get('intro', p['name']) + ' ' + ' · '.join(d.get('bullets', []))).strip()
    t = ('A refined essential for your daily rotation. A clean silhouette, comfortable stretch and thoughtful details. '
         'A modern neckline, practical storage and an easy silhouette, designed to feel comfortable through a busy day.')
    if p['category'] == 'Sets':
        t += ' Includes one scrub top and one pair of scrub trousers.'
    if p['category'] in ('Tops', 'Sets'):
        t += ' The top has three pockets: one on the chest and two discreet pockets at the sides.'
    if p['category'] in ('Pants', 'Sets'):
        t += (' The trousers have a zipped pocket on the right and two pockets at the back.' if p['fit'] == 'Men' else
              ' The trousers have a zipped pocket on the right, an open pocket on the left and two pockets at the back, with a side slit at each ankle.')
        t += ' A bleep and badge holder has been added to the trousers following feedback from our customers.'
    t += (f" Fabric: {MATERIAL}, 205 gsm, with four-way stretch. Sweat-absorbing and moisture-wicking, with a brushed finish."
          f" Machine wash at 40°C. {p['fit']}’s modern regular fit in sizes S to 2XL. Colour: {p['color']}.")
    return t

def stock_left():
    """Pieces left on the shelf, refreshed every five minutes."""
    if time.time() - _stock['at'] > 300:
        try:
            _stock.update(at=time.time(), left={r['key']: r['left'] for r in inventory.summary()})
        except Exception:
            _stock.update(at=time.time(), left={})
    return _stock['left']

def in_stock(p, size):
    left = stock_left()
    if not left:
        return True
    if p['device']:
        return left.get(inventory.key('Device', p['name'], 'Standard', 'device'), 0) > 0
    need = [n for c, n in (('Tops', 'top'), ('Pants', 'trousers')) if p['category'] in (c, 'Sets')]
    return all(left.get(inventory.key(p['fit'], p['color'], size, n), 0) > 0 for n in need)

def sizes(p):
    return ('Standard',) if p['device'] else shop.SIZES

def delivery_pence(p):
    uk = shop.DELIVERY['uk']
    return 0 if p['pence'] >= uk['free_from'] else uk['pence']

def money(pence):
    return '%d.%02d' % divmod(pence, 100)

# ---------------------------------------------------------------- product page
def _offer(p, size, link):
    uk = shop.DELIVERY['uk']
    return {'@type': 'Offer', 'url': link, 'priceCurrency': 'GBP', 'price': money(p['pence']),
            'itemCondition': 'https://schema.org/NewCondition',
            'availability': 'https://schema.org/' + ('InStock' if in_stock(p, size) else 'OutOfStock'),
            'seller': {'@id': SITE + '/#organization'},
            'shippingDetails': {'@type': 'OfferShippingDetails',
                                'shippingRate': {'@type': 'MonetaryAmount', 'value': money(delivery_pence(p)), 'currency': 'GBP'},
                                'shippingDestination': {'@type': 'DefinedRegion', 'addressCountry': 'GB'},
                                'deliveryTime': {'@type': 'ShippingDeliveryTime',
                                                 'handlingTime': {'@type': 'QuantitativeValue', 'minValue': 0, 'maxValue': 1, 'unitCode': 'DAY'},
                                                 'transitTime': {'@type': 'QuantitativeValue', 'minValue': uk['days'][0], 'maxValue': uk['days'][1], 'unitCode': 'DAY'}}},
            'hasMerchantReturnPolicy': {'@type': 'MerchantReturnPolicy', 'applicableCountry': 'GB',
                                        'returnPolicyCategory': 'https://schema.org/MerchantReturnFiniteReturnWindow',
                                        'merchantReturnDays': 30, 'returnMethod': 'https://schema.org/ReturnByMail',
                                        'returnFees': 'https://schema.org/ReturnFeesCustomerResponsibility'}}

def _variant(p, size, pics):
    link = url('p/' + p['id']) + ('' if p['device'] else '?size=' + size)
    v = {'@type': 'Product', 'name': title(p) + ('' if p['device'] else ' – Size ' + size), 'sku': sku(p, size),
         'description': description(p), 'image': [url(i) for i in pics[:6]], 'brand': {'@type': 'Brand', 'name': 'DocNova'},
         'offers': _offer(p, size, link)}
    if not p['device']:
        v.update(color=p['color'], size=size, material=MATERIAL,
                 audience={'@type': 'PeopleAudience', 'suggestedGender': 'female' if p['fit'] == 'Women' else 'male'})
    return v

def sku(p, size):
    return p['id'] if p['device'] else p['id'] + '-' + size.lower()

def structured(p, size=None):
    pics = images(p)
    if p['device']:
        data = _variant(p, 'Standard', pics)
    elif size in shop.SIZES:  # a link for one size shows that size only, so Google compares like with like
        data = {**_variant(p, size, pics), 'inProductGroupWithID': p['id']}
    else:
        data = {'@type': 'ProductGroup', 'name': title(p), 'description': description(p), 'productGroupID': p['id'],
                'url': url('p/' + p['id']), 'image': [url(i) for i in pics[:6]], 'brand': {'@type': 'Brand', 'name': 'DocNova'},
                'color': p['color'], 'material': MATERIAL, 'variesBy': ['https://schema.org/size'],
                'hasVariant': [_variant(p, s, pics) for s in shop.SIZES]}
    return json.dumps({'@context': 'https://schema.org', **data}, ensure_ascii=False).replace('</', '<\\/')

def product_page(pid, size=None):
    """The normal website page, opened on one product, with that product's name, price and picture in the page itself."""
    p = products().get(pid)
    if not p:
        return None
    size = size if size in sizes(p) and not p['device'] else None
    page = (ROOT / 'dist' / 'index.html').read_text(encoding='utf-8')
    e, pics = html.escape, images(p)
    name, desc, link, pic = title(p), description(p), url('p/' + p['id']), url(pics[0])
    short = desc if len(desc) < 300 else desc[:297].rsplit(' ', 1)[0] + '…'
    hop = '/#/product/' + p['id'] + ('?size=' + size if size else '')
    page = page.replace('<head>', '<head><base href="/"><script>history.replaceState(null,"",' + json.dumps(hop) + ')</script>', 1)
    page = re.sub(r'<title>.*?</title>', lambda m: '<title>' + e(name) + ' | DocNova</title>', page, count=1)
    page = re.sub(r'<link rel="canonical" href="[^"]*">', lambda m: '<link rel="canonical" href="' + link + '">', page, count=1)
    for prop, value in (('og:type', 'product'), ('og:title', name + ' | DocNova'), ('og:description', short), ('og:url', link), ('og:image', pic)):
        page = re.sub(r'<meta property="' + prop + r'" content="[^"]*">', lambda m: '<meta property="' + prop + '" content="' + e(value) + '">', page, count=1)
    page = re.sub(r'<meta property="og:image:(width|height)" content="[^"]*">', '', page)
    page = re.sub(r'<meta name="twitter:image" content="[^"]*">', lambda m: '<meta name="twitter:image" content="' + pic + '">', page, count=1)
    page = re.sub(r'<meta name="description" content="[^"]*">', lambda m: '<meta name="description" content="' + e(short) + '">', page, count=1)
    page = page.replace('</head>', '<script type="application/ld+json">' + structured(p, size) + '</script></head>', 1)
    m = re.search(r'(<main id="app"[^>]*>)(.*?)(</main>)', page, flags=re.S)
    stock = any(in_stock(p, s) for s in ([size] if size else sizes(p)))
    summary = ('<div class="pdp"><div><img class="gallery-main" src="' + e(pics[0]) + '" alt="' + e(name) + '"></div>'
               '<div class="product-detail"><span class="eyebrow">' + ('MEDICAL ESSENTIALS' if p['device'] else 'DOCNOVA · ' + e(p['fit'].upper())) + '</span>'
               '<h1>' + e(name) + '</h1><p class="price">£' + money(p['pence']) + '</p><p>' + e(desc) + '</p>'
               + ('' if p['device'] else '<p class="fine">Sizes: ' + ', '.join(shop.SIZES) + '</p>')
               + '<p class="fine">' + ('In stock' if stock else 'Out of stock') + ' · UK delivery ' + ('free' if not delivery_pence(p) else '£' + money(delivery_pence(p)) + ', or free on orders of £70 or more')
               + ' · 30-day returns</p></div></div>')
    # The home page banner is kept aside so the rest of the site still finds it when the visitor goes to Home.
    return page[:m.start()] + m.group(1) + summary + m.group(3) + '<template id="home-hero">' + m.group(2) + '</template>' + page[m.end():]

# ---------------------------------------------------------------- Google feed and sitemap
def _tag(name, value):
    return '<g:%s>%s</g:%s>' % (name, x(str(value)), name)

def feed():
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>',
           '<title>DocNova</title>', '<link>' + SITE + '/</link>',
           '<description>DocNova premium medical scrubs and medical essentials</description>']
    for p in products().values():
        if p['device'] and not FEED_DEVICES:
            continue
        pics = images(p)
        for size in sizes(p):
            link = url('p/' + p['id']) + ('' if p['device'] else '?size=' + size)
            item = ['<item>', _tag('id', sku(p, size)), _tag('title', title(p) + ('' if p['device'] else ' – Size ' + size)),
                    _tag('description', description(p)), _tag('link', link), _tag('image_link', url(pics[0]))]
            item += [_tag('additional_image_link', url(i)) for i in pics[1:11]]
            item += [_tag('availability', 'in_stock' if in_stock(p, size) else 'out_of_stock'), _tag('price', money(p['pence']) + ' GBP'),
                     _tag('brand', 'DocNova'), _tag('condition', 'new'), _tag('identifier_exists', 'no'),
                     '<g:shipping>' + _tag('country', 'GB') + _tag('service', 'UK delivery') + _tag('price', money(delivery_pence(p)) + ' GBP') + '</g:shipping>']
            if p['device']:
                item.append(_tag('product_type', 'Medical essentials'))
            else:
                item += [_tag('item_group_id', p['fit'].lower() + '-scrub-' + piece(p).lower()), _tag('color', p['color']), _tag('size', size),
                         _tag('gender', 'female' if p['fit'] == 'Women' else 'male'), _tag('age_group', 'adult'), _tag('material', MATERIAL),
                         _tag('google_product_category', 'Apparel & Accessories > Clothing > Uniforms'),
                         _tag('product_type', 'Scrubs > %s > %s' % (p['fit'], {'Tops': 'Tops', 'Pants': 'Trousers', 'Sets': 'Sets'}[p['category']]))]
            out.append(''.join(item) + '</item>')
    out.append('</channel></rss>')
    return '\n'.join(out)

def sitemap():
    day = time.strftime('%Y-%m-%d', time.gmtime((ROOT / 'dist' / 'app.js').stat().st_mtime))
    rows = ['<url><loc>%s/</loc><lastmod>%s</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>' % (SITE, day)]
    rows += ['<url><loc>%s</loc><lastmod>%s</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>' % (url('p/' + pid), day)
             for pid in products()]
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  ' + '\n  '.join(rows) + '\n</urlset>\n'
