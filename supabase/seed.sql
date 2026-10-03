-- Demo stockroom: 10 items, 4 below their minimum (OFF-NOTE-YEL has no shop mapping,
-- so the agent sees it as low but cannot order it).
-- Safe to re-run: existing SKUs are left untouched.

insert into items (sku, name, category, unit, location, quantity, min_qty, max_qty, shop_sku) values
  ('OFF-PAP-A4',   'Papier A4 500 ark.',             'papier',    'ream', 'Piętro 2 / Szafa B',   3, 10, 40, 'SHOP-1042'),
  ('OFF-TON-HP26', 'Toner HP 26A',                   'drukarka',  'pcs',  'Piętro 2 / Szafa B',   2,  2,  5, 'SHOP-2210'),
  ('OFF-PEN-BLU',  'Długopis niebieski',             'pisaki',    'pcs',  'Piętro 1 / Recepcja', 45, 20, 100, 'SHOP-3101'),
  ('OFF-PEN-BLK',  'Długopis czarny',                'pisaki',    'pcs',  'Piętro 1 / Recepcja',  8, 20, 100, 'SHOP-3102'),
  ('OFF-NOTE-YEL', 'Karteczki samoprzylepne żółte',  'papier',    'pack', 'Piętro 1 / Recepcja',  2,  5,  20, null),
  ('OFF-MRK-WB',   'Marker do tablicy (4 szt.)',     'pisaki',    'pack', 'Sala konferencyjna',   6,  3,  10, 'SHOP-3205'),
  ('OFF-BAT-AA',   'Baterie AA (4 szt.)',            'elektryka', 'pack', 'Piętro 2 / Szafa A',   1,  4,  12, 'SHOP-4401'),
  ('KIT-COF-1KG',  'Kawa ziarnista 1 kg',            'kuchnia',   'pcs',  'Kuchnia',              4,  3,  10, 'SHOP-5120'),
  ('KIT-TEA-100',  'Herbata czarna 100 torebek',     'kuchnia',   'pack', 'Kuchnia',              5,  2,   8, 'SHOP-5130'),
  ('OFF-ENV-C4',   'Koperty C4 (50 szt.)',           'papier',    'pack', 'Piętro 2 / Szafa A',   3,  2,  10, 'SHOP-1210')
on conflict (sku) do nothing;
