#!/usr/bin/env python3
"""Prepare one bounded local SoilGrids/JRC subset; no network or REST access.

Use provider-pinned raster files acquired separately through netfetch. Rasterio
is an offline preparation dependency; runtime lookup needs only NumPy.
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

import jrcwater
from provider_contract import prepare_raster
import soilgrids


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider', choices=('soilgrids', 'jrc_gsw'))
    parser.add_argument('layer', choices=('sand', 'clay', 'cfvo', 'occurrence', 'seasonality'))
    parser.add_argument('source', type=Path, help='local raster file, never a remote URI')
    parser.add_argument('output', type=Path, help='dedicated prepared semantic catalog')
    parser.add_argument('--bounds', type=float, nargs=4, metavar=('WEST', 'SOUTH', 'EAST', 'NORTH'), required=True)
    parser.add_argument('--shape', type=int, nargs=2, metavar=('ROWS', 'COLS'), default=(128, 128))
    parser.add_argument('--source-revision', required=True, help='pinned release or dated/checksummed snapshot')
    parser.add_argument('--acquired-at', required=True, help='actual acquisition timestamp, ISO 8601')
    parser.add_argument('--source-url', action='append', default=[], help='public source/evidence URL, no credentials')
    parser.add_argument('--period', help='JRC layer observation period, independently for each layer')
    parser.add_argument('--source-crs', help='override missing CRS with verified WKT/PROJ; never pseudo EPSG:152160')
    parser.add_argument('--source-nodata', type=float, help='explicit verified source nodata override')
    parser.add_argument('--band', type=int, default=1)
    args = parser.parse_args(argv)
    provider = soilgrids if args.provider == 'soilgrids' else jrcwater
    if args.layer not in provider.UNITS:
        parser.error('layer is incompatible with selected provider')
    if args.source_revision.strip().lower() in ('latest', 'current', 'unknown', ''):
        parser.error('source revision must identify a pinned release or snapshot')
    if args.provider == 'jrc_gsw' and (not args.period or not args.period.strip()):
        parser.error('JRC preparation requires the selected layer observation --period')
    if args.source_crs and '152160' in args.source_crs:
        parser.error('152160 is not an EPSG CRS; supply verified native IGH WKT/PROJ')
    for url in args.source_url:
        split = urlsplit(url)
        if (split.scheme not in ('https', 'http') or not split.netloc or split.username or split.password or
                any(k.lower() in ('token', 'api_key', 'apikey', 'access_token', 'authorization')
                    for k, _ in parse_qsl(split.query))):
            parser.error('source URL must be public and must not contain credentials')
    metadata = dict(source_revision=args.source_revision, license=provider.LICENSE,
                    acquired_at=args.acquired_at, units=provider.UNITS[args.layer],
                    nodata=args.source_nodata, resolution=250 if args.provider == 'soilgrids' else 30,
                    confidence='model_prediction' if args.provider == 'soilgrids' else 'historical_observations',
                    source_urls=args.source_url or [provider.SOURCE_URL])
    if args.provider == 'soilgrids':
        metadata.update(depth=soilgrids.DEPTH, statistic=soilgrids.STATISTIC)
    else:
        metadata.update(period=args.period, attribution=jrcwater.ATTRIBUTION,
                        role='supplementary_water_evidence', geometry_authority=False,
                        water_level_available=False)
    row = prepare_raster(args.source, args.output, args.provider, args.layer, args.bounds, args.shape,
                         metadata, band=args.band, src_crs=args.source_crs, src_nodata=args.source_nodata)
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
