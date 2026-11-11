#!/usr/bin/env python3
"""Build a genuine Three.js project, default zh-CN; explicit --lang selects localized content."""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from validate import validate
ROOT=Path(__file__).resolve().parents[1]
TEXT_KEYS={'name','description','title','evidence','stakeholders','concerns','questions','orderBy','boundary','layer','repo','runtime','environment','dataZone','trustZone','opsLayer','integrationLayer','extensionLayer','failureDomain','domain','owner','system','scope','sources','constraints','decisions','qualityAttributes','control','reviewIntent'}

def localize_model(model,lang):
    # Only localized text is replaced: entity IDs and architecture facts remain intact.
    src=json.loads(json.dumps(model,ensure_ascii=False))
    for o in [src.get('meta',{}),*src['nodes'],*src['edges'],*src['views']]:
        for field,value in o.get('i18n',{}).get(lang,{}).items():
            if field not in TEXT_KEYS:raise ValueError(f'字段 {field} 不允许通过翻译改变')
            o[field]=value
        o.pop('i18n',None)
    src['meta']['language']=lang
    return src

def run(argv=None):
    parser=argparse.ArgumentParser(description='Generate Three.js WebGL multi-view architecture site')
    parser.add_argument('model',type=Path,help='Single-source architecture.json')
    parser.add_argument('--out',type=Path,required=True,help='Empty destination folder')
    parser.add_argument('--lang',default='zh-CN',help='zh-CN (default), ja-JP, en-US; custom if UI/model resources are added')
    parser.add_argument('--strict',action='store_true',help='Require confirmed architecture evidence')
    args=parser.parse_args(argv)
    try:source=json.loads(args.model.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as e:print(f'ERROR {e}',file=sys.stderr);return 2
    errs,warns=validate(source,args.strict,args.lang)
    if errs:
        for error in errs:print('ERROR '+error,file=sys.stderr)
        return 1
    if args.lang not in ('zh-CN','ja-JP','en-US'):
        print(f'ERROR 缺少 {args.lang} 的网页语言资源。请先扩充 assets/viewer/src/i18n.js。',file=sys.stderr);return 1
    try:normalized=localize_model(source,args.lang)
    except ValueError as e:print('ERROR',e,file=sys.stderr);return 1
    out=args.out.resolve();template=(ROOT/'assets/viewer').resolve()
    if out==ROOT.resolve() or ROOT.resolve() in out.parents or out==template or template in out.parents:
        print('ERROR 输出目录不得覆盖 Skill 本身',file=sys.stderr);return 2
    if out.exists() and any(out.iterdir()):print('ERROR 输出目录必须为空',file=sys.stderr);return 2
    shutil.copytree(template,out,dirs_exist_ok=True,ignore=shutil.ignore_patterns('node_modules','dist','.vite','__pycache__'))
    (out/'architecture.json').write_text(json.dumps(normalized,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    # Only keys the viewer actually reads. A flag nobody consumes is worse than a
    # missing one: it invites the next person to trust a setting that does nothing.
    (out/'viewer-config.json').write_text(json.dumps({'language':args.lang},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    html=(out/'index.html').read_text(encoding='utf8')
    html=re.sub(r'<html lang="[^"]+">',f'<html lang="{args.lang}">',html,count=1)
    (out/'index.html').write_text(html,encoding='utf8')
    print(f'生成完成：{out}\n语言：{args.lang} · 渲染器：Three.js/WebGL 真实 3D + 原生 SVG 2D 平面（读者可切换，无 CSS 伪 3D）')
    print(f'运行：cd "{out}"; npm install; npm run dev')
    print('说明：当前环境若无法联网获取 npm 依赖，则需要在可连接 registry.npmjs.org 的环境安装。')
    for item in warns:print('WARN '+item)
    return 0
if __name__=='__main__':sys.exit(run())
