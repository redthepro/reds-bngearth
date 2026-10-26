"""Landmark conversion contracts: colors, transforms, bounds and actual compiled camera LODs."""
import argparse
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'server'))
import convert_landmark as converter
import landmark_textures
import cdae


def tetrahedron():
    return dict(positions=np.array([[0., 0., 0.], [2., 0., 0.], [0., 2., 0.], [0., 0., 2.]]),
                normals=np.tile([1., 0., 0.], (4, 1)), uvs=np.zeros((4, 2)),
                tris=np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]), tri_materials=np.zeros(4, int))


def simple_glb(path, mesh=None):
    mesh = tetrahedron() if mesh is None else mesh
    vertices = mesh['positions'].astype('<f4').tobytes()
    indices = mesh['tris'].astype('<u2').tobytes()
    data = vertices + indices
    document = dict(asset={'version': '2.0'}, scene=0, scenes=[{'nodes': [0]}], nodes=[{'mesh': 0}],
                    meshes=[{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
                    materials=[{'name': 'Stone', 'pbrMetallicRoughness': {'baseColorFactor': [.5, .5, .5, 1]}}],
                    buffers=[{'byteLength': len(data)}], bufferViews=[
                        {'buffer': 0, 'byteOffset': 0, 'byteLength': len(vertices)},
                        {'buffer': 0, 'byteOffset': len(vertices), 'byteLength': len(indices)}],
                    accessors=[{'bufferView': 0, 'componentType': 5126, 'count': len(mesh['positions']), 'type': 'VEC3',
                                'min': [0, 0, 0], 'max': [2, 2, 2]},
                               {'bufferView': 1, 'componentType': 5123, 'count': mesh['tris'].size, 'type': 'SCALAR'}])
    converter.write_glb(path, document, [(0x004E4942, data)])


class ConverterTest(unittest.TestCase):
    def test_cooked_dds_formats_mips_and_channel_values(self):
        cases = [('color', 'RGBA', (80, 130, 200, 255), 99, 16),
                 ('normal', 'RGB', (160, 100, 250), 83, 16),
                 ('data', 'L', 90, 80, 8)]
        for semantic, mode, value, dxgi, block_size in cases:
            with self.subTest(semantic=semantic):
                data = landmark_textures.encode_dds(Image.new(mode, (32, 16), value), semantic)
                self.assertEqual(data[84:88], b'DX10')
                self.assertEqual(struct.unpack_from('<I', data, 128)[0], dxgi)
                self.assertEqual(struct.unpack_from('<I', data, 28)[0], 6)
                expected = sum(max(1, (max(1, 32 >> k) + 3) // 4) *
                               max(1, (max(1, 16 >> k) + 3) // 4) * block_size for k in range(6))
                self.assertEqual(len(data), 148 + expected)
                # Pillow independently decodes both the existing native BC7 and its BC4/5 output.
                decoded = np.asarray(Image.open(io.BytesIO(data)))
                if semantic == 'normal':
                    decoded, value = decoded[..., :2], value[:2]
                self.assertLessEqual(np.abs(decoded.astype(int) - value).max(), 2)

    def test_mips_preserve_linear_data_srgb_color_and_unit_normals(self):
        checker = np.indices((16, 16)).sum(0) % 2 * 255
        color = Image.fromarray(np.repeat(checker[..., None], 3, axis=2).astype(np.uint8), 'RGB')
        self.assertAlmostEqual(int(list(landmark_textures.texture_mips(color, 'color'))[-1][0, 0, 0]), 188, delta=1)
        data = Image.fromarray(checker.astype(np.uint8), 'L')
        self.assertAlmostEqual(int(list(landmark_textures.texture_mips(data, 'data'))[-1][0, 0, 0]), 128, delta=1)
        normal = Image.new('RGB', (16, 16), (190, 128, 238))
        mip = list(landmark_textures.texture_mips(normal, 'normal'))[-1][0, 0].astype(float) / 127.5 - 1
        self.assertAlmostEqual(float(np.linalg.norm(mip)), 1., delta=.01)

    def test_constant_color_is_encoded_as_srgb_once(self):
        document = {'materials': [{'name': 'Stone', 'pbrMetallicRoughness': {
            'baseColorFactor': [.5, .25, 0., 1.], 'metallicFactor': 0}}]}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / 'textures').mkdir()
            materials, records, _, warnings = converter.material_assets(document, [], out, '/art/test', 'test', 2048)
            material = materials[records[0]['engine_name']]
            image = Image.open(out / 'textures' / Path(material['Stages'][0]['baseColorMap']).name)
            self.assertEqual(image.size, (16, 16))
            self.assertEqual(image.getpixel((0, 0)), (188, 137, 0, 255))
            self.assertEqual(material['Stages'][0]['baseColorFactor'], [1, 1, 1, 1])
            self.assertEqual(records[0]['source_name'], 'Stone')
            self.assertEqual(warnings, [])

    def test_packed_data_channels_stay_linear(self):
        blob = io.BytesIO()
        Image.new('RGBA', (16, 16), (7, 90, 181, 255)).save(blob, format='PNG')
        document = dict(materials=[{'name': 'PBR', 'pbrMetallicRoughness': {'metallicRoughnessTexture': {'index': 0}}}],
                        textures=[{'source': 0}], images=[{'bufferView': 0}],
                        bufferViews=[{'byteLength': len(blob.getvalue())}])
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / 'textures').mkdir()
            materials, records, _, _ = converter.material_assets(document, [(0x004E4942, blob.getvalue())], out, '/art/test', 'test', 2048)
            stage = materials[records[0]['engine_name']]['Stages'][0]
            for key, expected in [('roughnessMap', 90), ('metallicMap', 181)]:
                image = Image.open(out / 'textures' / Path(stage[key]).name)
                self.assertEqual(image.mode, 'L')
                self.assertEqual(image.getpixel((0, 0)), expected)
        with self.assertRaisesRegex(ValueError, 'UV0'):
            converter.texture_image(document, [], {'index': 0, 'texCoord': 1})

    def test_uniform_scale_origin_and_normal_rotation(self):
        main, far = tetrahedron(), tetrahedron()
        transform = converter.transform_geometry([main, far], target_height=10., yaw=90., center='base')
        self.assertEqual(transform['scale'], 5.)
        np.testing.assert_allclose(main['positions'].min(0), [-5, -5, 0], atol=1e-10)
        np.testing.assert_allclose(main['positions'].max(0), [5, 5, 10], atol=1e-10)
        np.testing.assert_allclose(main['normals'], np.tile([0, 1, 0], (4, 1)), atol=1e-10)
        np.testing.assert_array_equal(main['positions'], far['positions'])
        converter.validate_mesh(main, ['stone'])

    def test_invalid_indices_and_vertex_limit_are_rejected(self):
        mesh = tetrahedron()
        mesh['tris'][0, 0] = 4
        with self.assertRaisesRegex(ValueError, 'indices'):
            converter.validate_mesh(mesh, ['stone'])
        mesh = tetrahedron()
        mesh['positions'] = np.zeros((65001, 3))
        with self.assertRaisesRegex(ValueError, '65000'):
            converter.validate_mesh(mesh, ['stone'])

    def test_tilt_is_removed_before_height_and_explicit_anchor(self):
        mesh = tetrahedron()
        original = mesh['positions'].copy()
        rotate = converter.orientation_matrix(tilt_x=17.)
        mesh['positions'] = original @ rotate.T + [4., 8., 12.]
        mesh['normals'] = mesh['normals'] @ rotate.T
        anchor = np.array([4., 8., 12.]) @ rotate
        transform = converter.transform_geometry([mesh], target_height=10., tilt_x=-17., origin_xy=anchor[:2])
        np.testing.assert_allclose(mesh['positions'], original * 5., atol=1e-10)
        self.assertAlmostEqual(transform['scale'], 5.)
        np.testing.assert_allclose(mesh['normals'], np.tile([1, 0, 0], (4, 1)), atol=1e-10)

    def test_alternate_uv_channels_require_exact_equality(self):
        uv = np.array([[0, 0], [.2, .8], [1, 1]], '<f4')
        blob = uv.tobytes() * 2
        document = dict(materials=[{'name': 'test', 'normalTexture': {'index': 0, 'texCoord': 1}}],
                        meshes=[{'primitives': [{'material': 0, 'attributes': {'TEXCOORD_0': 0, 'TEXCOORD_1': 1}}]}],
                        accessors=[dict(type='VEC2', componentType=5126, count=3, bufferView=i) for i in range(2)],
                        bufferViews=[dict(byteOffset=i*uv.nbytes, byteLength=uv.nbytes) for i in range(2)])
        warnings = converter.normalize_duplicate_uvs(document, [(0x004E4942, blob)])
        self.assertEqual(document['materials'][0]['normalTexture']['texCoord'], 0)
        self.assertIn('duplicate UV1', warnings[0])
        document['materials'][0]['normalTexture']['texCoord'] = 1
        uv[0, 0] = .1
        with self.assertRaisesRegex(ValueError, 'exactly duplicate'):
            converter.normalize_duplicate_uvs(document, [(0x004E4942, blob[:uv.nbytes] + uv.tobytes())])

    def test_glb_roundtrip_and_attribution_required_before_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.glb'
            simple_glb(source)
            document, chunks, digest = converter.read_glb(source)
            self.assertEqual(document['materials'][0]['name'], 'Stone')
            self.assertEqual(len(digest), 64)
            self.assertEqual(len(chunks), 1)
            args = argparse.Namespace(source=str(source), metadata=None, creator=None, license=None, source_url=None)
            with self.assertRaisesRegex(ValueError, 'creator'):
                converter.convert(args)


BLENDER = os.environ.get('BNGE_TEST_BLENDER')


@unittest.skipUnless(BLENDER, 'set BNGE_TEST_BLENDER to run the real Blender/CDAE integration test')
class BlenderIntegrationTest(unittest.TestCase):
    def test_oriented_crop_removes_outside_geometry_before_lod_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.glb'
            # A cube retains enough faces after clipping to support a decimated LOD.
            cube = dict(positions=np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0],
                                            [0, 0, 2], [2, 0, 2], [2, 2, 2], [0, 2, 2]]),
                        tris=np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                                       [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                                       [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]))
            simple_glb(source, cube)
            args = argparse.Namespace(source=str(source), metadata=None, creator='Converter test',
                                      license='CC0-1.0', source_url='https://example.test/fixture',
                                      out=str(root / 'out'), asset_id='crop', virtual_path=None,
                                      max_texture=16, blender=BLENDER, max_triangles=40000, far_triangles=5000,
                                      far_ratio=.75, scale=1., target_height=None, target_width=None,
                                      yaw=0., center='origin', lod_pixels=300., preserve_simple_max=0,
                                      crop_min=[.5, -3., -1.], crop_max=[3., 3., 3.])
            result = converter.convert(args)
            self.assertAlmostEqual(result['bounds'][0][0], .5, places=5)
            self.assertLess(result['geometry']['far_triangles'], result['geometry']['main_triangles'])
            self.assertEqual(result['transform']['crop_oriented_bounds'], [args.crop_min, args.crop_max])

    def test_attributed_compiled_shape_has_two_reduced_camera_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.glb'
            simple_glb(source)
            args = argparse.Namespace(source=str(source), metadata=None, creator='Converter test',
                                      license='CC0-1.0', source_url='https://example.test/fixture',
                                      out=str(root / 'out'), asset_id='fixture', virtual_path=None,
                                      max_texture=16, blender=BLENDER, max_triangles=40000, far_triangles=5000,
                                      far_ratio=.75, scale=1., target_height=12., target_width=None,
                                      yaw=30., center='base', lod_pixels=300., preserve_simple_max=0)
            result = converter.convert(args)
            shape = cdae.read(root / 'out' / 'compiled.cdae')
            self.assertEqual(len(shape['meshes']), 2)
            self.assertEqual(shape['details'][0], 2)
            self.assertIn('detail300', shape['names'])
            self.assertIn('detail2', shape['names'])
            self.assertLess(result['geometry']['far_triangles'], result['geometry']['main_triangles'])
            self.assertAlmostEqual(result['bounds'][1][2], 12.)
            self.assertEqual(result['attribution']['creator'], 'Converter test')
            materials = json.loads((root / 'out' / 'main.materials.json').read_text())
            for material in materials.values():
                self.assertEqual(material['name'], material['mapTo'])
                self.assertTrue(material['Stages'][0]['baseColorMap'].startswith(
                    '/levels/beamng_earth/art/shapes/beamngEarth/landmarks/fixture/'))
            self.assertFalse((root / 'out' / 'conversion.log').exists())
            self.assertEqual(len(list((root / 'out' / 'textures').iterdir())), 1)
            self.assertEqual(len(list((root / 'out' / 'textures').glob('*.dds'))), 1)
            self.assertEqual(result['texture_format'], 'dds')
            # Fully preserving a tiny source cannot meet a reduced LOD budget: fail clearly.
            args.out = str(root / 'too_small')
            args.preserve_simple_max = 128
            with self.assertRaises(RuntimeError):
                converter.convert(args)
            self.assertIn('below the preserved simple geometry', (root / 'too_small' / 'conversion.log').read_text())


if __name__ == '__main__':
    unittest.main()
