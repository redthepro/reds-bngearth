"""Offline asset admission/transport/resource safety, with authored local fixtures."""
import copy
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
import zipfile

import numpy as np
from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / 'tools')]
import assetcatalog as ac
import prepare_asset as prep
import sketchfab_acquire as fetch

PACKAGE = Path(__file__).resolve().parents[2] / 'docs' / 'earth2'
BUDGETS = json.loads((PACKAGE / 'config/05-asset-budgets.json').read_text(encoding='utf-8'))
TEMPLATE = json.loads((PACKAGE / 'config/asset-manifest.example.json').read_text(encoding='utf-8'))
UID = '0123456789abcdef0123456789abcdef'


def manifest(fixture=False):
    result = copy.deepcopy(TEMPLATE)
    result.update(example_only=False, asset_id='authored_box' if fixture else 'test_bollard')
    result['source'].update(provider='local_fixture' if fixture else 'sketchfab', model_uid=UID,
        model_name='Authored mirrored unit box' if fixture else 'Offline test asset',
        model_url='https://sketchfab.com/3d-models/offline-test-' + UID,
        creator_name='BeamNG Earth 2 analytic fixture', creator_url='https://example.org/analytic-fixtures')
    result['license'].update(id='cc0', label='CC0 1.0', url=ac.LICENSES['cc0'],
        attribution='Authored analytic geometry released under CC0 1.0; not a Sketchfab acquisition.',
        redistribution_verified=True, derivatives_verified=True, verified_at='2026-10-26T00:00:00+00:00',
        evidence_sha256=hashlib.sha256(b'Authored analytic geometry; CC0 1.0').hexdigest())
    result['runtime']['fixture_only'] = fixture
    return result


def prepared_manifest_with_missing_files():
    result=manifest()
    result['state']='prepared_engine_pending'
    result['source'].update(downloaded_at='2026-10-26T00:00:00+00:00', archive_sha256='a'*64, archive_bytes=12)
    result['geometry'].update(source_units='meters',source_up_axis='+Y',dimensions_m=[1,1,1])
    result['runtime'].update(approved=True,artifact_root='prepared/missing',
        lods=[dict(artifact=f'lod{i}.dae',sha256='b'*64,bytes=100,triangles=12//(i+1),vertices=36) for i in range(3)],
        materials=[dict(map_to='material',artifact='main.materials.json',sha256='c'*64,bytes=100)],
        textures=[],collision=dict(mode='none',artifact=None,triangles=0))
    result['quality'].update(geometry_validated=True,textures_validated=True,engine_validated=False)
    result['processing'].update(tool_versions={'blender':'5.2.2'},changes=['test'],prepared_at='2026-10-26T00:00:00+00:00',output_sha256='d'*64)
    return result


def make_fixture(root):
    """An original CC0 cube with nonuniform mirrored transform and one PNG."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    positions = np.array([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],
                          [-.5,-.5,.5],[.5,-.5,.5],[.5,.5,.5],[-.5,.5,.5]],dtype='<f4')
    faces = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                      [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]],dtype='<u2')
    uv = np.array([[0,0],[1,0],[1,1],[0,1]] * 2,dtype='<f4')
    raw = positions.tobytes() + uv.tobytes() + faces.tobytes()
    (root/'scene.bin').write_bytes(raw)
    Image.new('RGBA',(16,16),(80,160,210,255)).save(root/'base.png')
    model = {'asset':{'version':'2.0','generator':'BeamNG Earth 2 authored CC0 analytic fixture'},
        'buffers':[{'uri':'scene.bin','byteLength':len(raw)}],
        'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':positions.nbytes},
                       {'buffer':0,'byteOffset':positions.nbytes,'byteLength':uv.nbytes},
                       {'buffer':0,'byteOffset':positions.nbytes+uv.nbytes,'byteLength':faces.nbytes}],
        'accessors':[{'bufferView':0,'componentType':5126,'count':8,'type':'VEC3','min':[-.5]*3,'max':[.5]*3},
                     {'bufferView':1,'componentType':5126,'count':8,'type':'VEC2'},
                     {'bufferView':2,'componentType':5123,'count':36,'type':'SCALAR'}],
        'images':[{'uri':'base.png'}],'textures':[{'source':0}],
        'materials':[{'name':'analytic_blue','pbrMetallicRoughness':{'baseColorTexture':{'index':0},'metallicFactor':0}}],
        'meshes':[{'primitives':[{'attributes':{'POSITION':0,'TEXCOORD_0':1},'indices':2,'material':0}]}],
        'nodes':[{'mesh':0,'scale':[-2,3,4],'translation':[10,20,30]}], 'scenes':[{'nodes':[0]}],'scene':0}
    ac.atomic_json(root/'scene.gltf',model)
    ac.atomic_json(root/'manifest.json',manifest(True))
    (root/'LICENSE.txt').write_text('Authored analytic geometry; CC0 1.0\nhttps://creativecommons.org/publicdomain/zero/1.0/\n',encoding='utf-8')
    return root/'scene.gltf'


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}


class Fetcher:
    @contextmanager
    def session(self,url):
        class Session:
            status,nbytes=200,0
        yield Session()


class Opener:
    def __init__(self,responses):
        self.responses,self.requests=list(responses),[]
    def open(self,request,timeout):
        self.requests.append(request)
        response=self.responses.pop(0)
        if isinstance(response,Exception):
            raise response
        return response


class AdmissionTest(unittest.TestCase):
    def test_examples_and_incomplete_models_fail_closed(self):
        with self.assertRaises(ValueError): ac.validate_manifest(TEMPLATE,budgets=BUDGETS)
        for field in ('model_uid','model_name','creator_name','creator_url','model_url'):
            value=manifest();value['source'][field]=None
            with self.subTest(field=field),self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)

    def test_license_allowlist_cannot_be_overridden_by_booleans(self):
        for identifier in ('cc-by-sa','cc-by-nc','cc-by-nd','unknown','editorial'):
            value=manifest();value['license']['id']=identifier
            with self.subTest(identifier=identifier),self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)
        value=manifest();value['license']['url']='https://example.org/cc0'
        with self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)

    def test_valid_license_has_timestamp_hash_and_attribution(self):
        self.assertEqual(ac.license_status(manifest()['license']),'eligible')
        for field in ('verified_at','evidence_sha256','attribution'):
            value=manifest();value['license'][field]=None
            with self.subTest(field=field),self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)

    def test_fixture_explicitly_excluded_from_runtime_catalog(self):
        with self.assertRaises(ValueError): ac.validate_manifest(manifest(True),budgets=BUDGETS)
        ac.validate_manifest(manifest(True),allow_fixture=True,budgets=BUDGETS)
        with tempfile.TemporaryDirectory() as root:
            result=ac.write_catalog(root,[manifest(True)],BUDGETS)
            self.assertEqual(result['admitted'],0)
            self.assertFalse(ac.Catalog(root,BUDGETS).candidates('roadside.barrier'))

    def test_canonical_order_and_tamper_detection(self):
        self.assertEqual(ac.canonical({'a':1,'b':2}),ac.canonical({'b':2,'a':1}))
        with tempfile.TemporaryDirectory() as root:
            first=ac.write_catalog(root,[],BUDGETS)
            self.assertEqual(first,ac.write_catalog(root,[],BUDGETS))
            path=Path(root)/'catalog.json';value=json.loads(path.read_text());value['fallback']='missing';ac.atomic_json(path,value)
            with self.assertRaises(ValueError): ac.Catalog(root,BUDGETS)

    def test_removed_prepared_artifacts_drop_asset_to_procedural_fallback(self):
        value=prepared_manifest_with_missing_files()
        ac.validate_manifest(value,prepared=True,budgets=BUDGETS)
        content={'schema_version':ac.SCHEMA,'assets':[value],'fallback':'procedural'}
        content['revision']=hashlib.sha256(ac.canonical(content)).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            ac.atomic_json(Path(root)/'catalog.json',content)
            catalog=ac.Catalog(root,BUDGETS)
            self.assertFalse(catalog.candidates('roadside.barrier'))
            self.assertEqual(catalog.revision,content['revision'])

    def test_semantic_budget_and_procedural_fallback(self):
        value=manifest();value['semantic']['categories']=['vegetation.tree.needleleaf']
        with self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)
        value=manifest();value['semantic']['fallback']='none'
        with self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)

    def test_signed_urls_not_admitted_to_manifests(self):
        value=manifest();value['source']['model_url'] += '?temporary=private'
        with self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)

    def test_malformed_nested_sections_and_integer_version_types_fail_closed(self):
        for section, bad in [('source', []), ('semantic', None), ('runtime', 'runtime'),
                             ('license', []), ('geometry', []), ('quality', None), ('processing', [])]:
            value=manifest()
            value[section]=bad
            with self.subTest(section=section), self.assertRaises(ValueError):
                ac.validate_manifest(value, prepared=section in ('geometry','quality','processing'), budgets=BUDGETS)
        value=manifest();value['schema_version']=True
        with self.assertRaises(ValueError): ac.validate_manifest(value,budgets=BUDGETS)
        value=manifest();value['geometry']['normalization_version']=True
        with self.assertRaises(ValueError): ac.validate_manifest(value,prepared=True,budgets=BUDGETS)
        value=manifest();value['processing']['recipe_version']=True
        with self.assertRaises(ValueError): ac.validate_manifest(value,prepared=True,budgets=BUDGETS)


class ArchiveTest(unittest.TestCase):
    def archive(self,root,name,body=b'abc',mode=None):
        path=Path(root)/'source.zip'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as output:
            info=zipfile.ZipInfo(name)
            if mode is not None:info.external_attr=mode<<16
            output.writestr(info,body)
        return path

    def test_safe_extraction_preserves_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.archive(root,'textures/a.png')
            result=ac.safe_extract(path,Path(root)/'new',BUDGETS['acquisition'])
            self.assertEqual((Path(root)/'new/textures/a.png').read_bytes(),b'abc')
            self.assertEqual(result['archive_sha256'],ac.sha256(path))

    def test_paths_special_files_and_scripts_rejected(self):
        for name,mode in [('../a.bin',None),('/a.bin',None),('C:/a.bin',None),('../a\\b.bin',None),
                          ('NUL.bin',None),('a.bin ',None),('model.py',None),('link.bin',stat.S_IFLNK|0o777)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as root:
                path=self.archive(root,name,mode=mode)
                with self.assertRaises(ValueError): ac.safe_extract(path,Path(root)/'new',BUDGETS['acquisition'])
        with self.assertRaises(ValueError): ac.safe_relative('a\\b.bin')

    def test_case_collisions_and_expansion_budget_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source.zip'
            with zipfile.ZipFile(path,'w') as output:
                output.writestr('A.bin',b'1');output.writestr('a.bin',b'2')
            with self.assertRaises(ValueError):ac.safe_extract(path,Path(root)/'new',BUDGETS['acquisition'])
            limits=dict(BUDGETS['acquisition'],expanded_bytes_per_asset=2)
            path=self.archive(root,'big.bin',b'abc')
            with self.assertRaises(ValueError):ac.safe_extract(path,Path(root)/'new',limits)


class ResourceTest(unittest.TestCase):
    def test_authored_gltf_and_missing_texture(self):
        with tempfile.TemporaryDirectory() as root:
            scene=make_fixture(root)
            data,resources=ac.validate_gltf(scene)
            self.assertEqual(len(resources),2)
            (Path(root)/'base.png').unlink()
            with self.assertRaises(ValueError):ac.validate_gltf(scene)

    def test_remote_and_traversing_resources_rejected(self):
        for uri in ('../outside.png','https://example.org/a.png','%2e%2e/a.png','C:/a.png','data:image/png;base64,AA=='):
            with self.subTest(uri=uri),tempfile.TemporaryDirectory() as root:
                scene=make_fixture(root);value=json.loads(scene.read_text());value['images'][0]['uri']=uri;ac.atomic_json(scene,value)
                with self.assertRaises(ValueError):ac.validate_gltf(scene)

    def test_malformed_gltf_nested_sections_fail_closed(self):
        for mutate in (
            lambda d: d.update(asset=[]),
            lambda d: d.update(extensionsRequired='not-an-array'),
            lambda d: d.update(buffers=[None]),
            lambda d: d.update(meshes=[None]),
            lambda d: d.update(accessors=[None]),
            lambda d: d['meshes'][0].update(primitives=[None]),
            lambda d: d.update(materials=[{'pbrMetallicRoughness': []}]),
            lambda d: d.update(materials=[{'extensions': {'KHR_materials_pbrSpecularGlossiness': []}}]),
            lambda d: d['meshes'][0]['primitives'][0].update(attributes=[]),
        ):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as root:
                scene=make_fixture(root); value=json.loads(scene.read_text()); mutate(value); ac.atomic_json(scene,value)
                with self.assertRaises(ValueError): ac.validate_gltf(scene)

    def test_simple_collision_is_grounded_outward_and_bounded(self):
        g=prep.box_geometry([2,4,3]);p=g['positions'][g['tris']]
        self.assertEqual(len(p),12);self.assertEqual(g['positions'][:,2].min(),0)
        self.assertGreater(np.sum(np.einsum('ij,ij->i',p[:,0],np.cross(p[:,1],p[:,2])))/6,0)


class TransferTest(unittest.TestCase):
    def test_explicit_auth_mode_never_reinterprets_personal_key(self):
        self.assertTrue(fetch.authorization('api-token',{'SKETCHFAB_API_TOKEN':'test-placeholder'})['Authorization'].startswith('Token '))
        with self.assertRaises(fetch.AcquisitionError):fetch.authorization('oauth',{'SKETCHFAB_API_TOKEN':'test-placeholder'})
        self.assertTrue(fetch.authorization('oauth',{'SKETCHFAB_OAUTH_TOKEN':'test-placeholder'})['Authorization'].startswith('Bearer '))

    def test_descriptor_rejects_denied_or_malformed_results(self):
        denied=HTTPError('https://api.sketchfab.com/',403,'denied',{},None)
        with self.assertRaises(fetch.AcquisitionError):fetch.download_descriptor(UID,'oauth',Fetcher(),Opener([denied]),{'SKETCHFAB_OAUTH_TOKEN':'test-placeholder'})
        record={'gltf':{'size':1,'expires':300,'url':'http://127.0.0.1/private'}}
        with self.assertRaises(fetch.AcquisitionError):fetch.download_descriptor(UID,'oauth',Fetcher(),Opener([Response(json.dumps(record).encode())]),{'SKETCHFAB_OAUTH_TOKEN':'test-placeholder'})

    def test_live_metadata_must_match_model_and_creator_attribution(self):
        value=manifest()
        metadata={'uid':UID,'isDownloadable':True,'name':value['source']['model_name'],
                  'license':{'url':value['license']['url']},
                  'user':{'profileUrl':value['source']['creator_url'],'displayName':value['source']['creator_name']}}
        verify=lambda data: fetch.verify_public_metadata(value,Fetcher(),Opener([Response(json.dumps(data).encode())]))
        self.assertTrue(verify(metadata)['is_downloadable'])
        for field, bad in (('name','Stale model name'),):
            altered=copy.deepcopy(metadata);altered[field]=bad
            with self.subTest(field=field),self.assertRaises(fetch.AcquisitionError):verify(altered)
        altered=copy.deepcopy(metadata);altered['user']['displayName']='Stale creator name'
        with self.assertRaises(fetch.AcquisitionError):verify(altered)
        altered=copy.deepcopy(metadata);altered['license']['url']=17
        with self.assertRaises(fetch.AcquisitionError):verify(altered)

    def test_download_hash_budget_and_no_auth_on_archive(self):
        with tempfile.TemporaryDirectory() as root:
            opener=Opener([Response(b'hello',headers={'Content-Length':'5','ETag':'"v1"'})])
            descriptor={'url':'https://sketchfab-prod-media.s3.amazonaws.com/a?temporary=opaque','size':5,'deadline':time.monotonic()+60}
            result=fetch.download_archive(UID,descriptor,root,BUDGETS['acquisition'],Fetcher(),opener)
            self.assertEqual(result['sha256'],hashlib.sha256(b'hello').hexdigest())
            self.assertEqual(result['actual_batch_download_bytes'],5)
            self.assertIsNone(opener.requests[0].get_header('Authorization'))
            for path in Path(root).glob('*.json'):self.assertNotIn('temporary',path.read_text())

    def test_expired_url_and_size_limit_do_not_publish_original(self):
        with tempfile.TemporaryDirectory() as root:
            descriptor={'url':'https://sketchfab-prod-media.s3.amazonaws.com/a','size':5,'deadline':time.monotonic()-1}
            with self.assertRaises(fetch.DownloadExpired):fetch.download_archive(UID,descriptor,root,BUDGETS['acquisition'],Fetcher(),Opener([]))
            descriptor['deadline']=time.monotonic()+60;descriptor['size']=BUDGETS['acquisition']['archive_bytes_per_asset']+1
            with self.assertRaises(fetch.AcquisitionError):fetch.download_archive(UID,descriptor,root,BUDGETS['acquisition'],Fetcher(),Opener([]))
            self.assertFalse(list(Path(root).glob('*.zip')))

    def test_interrupted_transfer_resumes_only_same_etag_and_range(self):
        with tempfile.TemporaryDirectory() as root:
            descriptor={'url':'https://sketchfab-prod-media.s3.amazonaws.com/a','size':5,'deadline':time.monotonic()+60}
            with self.assertRaises(fetch.AcquisitionError):fetch.download_archive(UID,descriptor,root,BUDGETS['acquisition'],Fetcher(),
                Opener([Response(b'he',headers={'Content-Length':'5','ETag':'"v1"'})]))
            opener=Opener([Response(b'llo',206,{'Content-Length':'3','Content-Range':'bytes 2-4/5','ETag':'"v1"'})])
            result=fetch.download_archive(UID,descriptor,root,BUDGETS['acquisition'],Fetcher(),opener)
            self.assertEqual(result['actual_batch_download_bytes'],5)
            self.assertEqual(opener.requests[0].get_header('Range'),'bytes=2-')
            self.assertEqual(Path(result['archive']).read_bytes(),b'hello')


if __name__ == '__main__':
    unittest.main()
