"""把已核对的研究数据绘成中文PNG，不计算估值、选择假设或下载数据。"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

from data_sources.paths import workspace_path

CONTRACT = 'alphalake-research-charts-v1'
COLORS = ['#2563a6', '#d78a24', '#31856a', '#9a5e91', '#697586']


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def numeric(value, nullable=False):
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('图表金额必须是有限数值；缺项使用null，不补零')


def validate(payload, workspace):
    if payload.get('contract') != CONTRACT or not isinstance(payload.get('charts'), list) or not payload['charts']:
        raise ValueError('非空研究图表契约 required')
    if not isinstance(payload.get('company'), str) or not payload['company'].strip():
        raise ValueError('company required')
    sources = payload.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('图表必须有真实来源文件、SHA-256与定位')
    verified = {}
    for source in sources:
        path = Path(source['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in str(path):
            raise ValueError('来源必须是workspace内相对路径')
        actual = (workspace / path).resolve(strict=True)
        if not actual.is_relative_to(workspace.resolve()) or not actual.is_file():
            raise ValueError('来源越出workspace或不是文件')
        if not source.get('label') or not source.get('locator') or source.get('sha256') != digest(actual):
            raise ValueError('来源标签、定位缺失或SHA-256不匹配')
        if source['id'] in verified:
            raise ValueError('重复来源ID')
        verified[source['id']] = actual
    ids = set()
    for chart in payload['charts']:
        identifier = chart.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', identifier) or identifier in ids:
            raise ValueError('非法或重复图表ID')
        ids.add(identifier)
        for key in ('title', 'unit', 'period', 'takeaway'):
            if not isinstance(chart.get(key), str) or not chart[key].strip():
                raise ValueError('每图必须有标题、单位、期间与一句话解读')
        if not chart.get('source_ids') or any(s not in verified for s in chart['source_ids']):
            raise ValueError('图表来源未登记')
        labels = chart.get('labels')
        if not isinstance(labels, list) or not labels or any(not isinstance(s, str) or not s for s in labels):
            raise ValueError('分类标签 required')
        kind = chart.get('kind')
        if kind in ('line', 'bar'):
            if not chart.get('series'):
                raise ValueError('图表序列 required')
            for series in chart['series']:
                if (not series.get('label') or series.get('role') not in ('reported', 'forecast', 'reference')
                        or len(series['values']) != len(labels)):
                    raise ValueError('序列必须标明名称、事实/预测/参考角色及完整标签长度')
                for value in series['values']:
                    numeric(value, nullable=True)
                if not any(v is not None for v in series['values']):
                    raise ValueError('全空序列不能绘成零线')
        elif kind == 'waterfall':
            values, types = chart['values'], chart['types']
            if len(values) != len(labels) or len(types) != len(labels) or types[0] != 'total' or types[-1] != 'total':
                raise ValueError('价值桥必须提供起点、变动和终点')
            running = 0
            for i, (value, typ) in enumerate(zip(values, types)):
                numeric(value)
                if typ == 'total':
                    if i > 0 and not math.isclose(running, value, rel_tol=1e-8, abs_tol=1e-7):
                        raise ValueError('价值桥的已有结果与组成不闭合；禁止用残差补项目')
                    running = value
                elif typ == 'change':
                    running += value
                else:
                    raise ValueError('未知价值桥项目类型')
        elif kind == 'heatmap':
            rows, matrix = chart['row_labels'], chart['values']
            if not rows or len(matrix) != len(rows) or any(len(row) != len(labels) for row in matrix):
                raise ValueError('敏感性矩阵维度不一致')
            for row in matrix:
                for value in row:
                    numeric(value)
        else:
            raise ValueError('只支持line/bar/waterfall/heatmap')
    return verified


def chinese_font(payload):
    from matplotlib import font_manager
    text = json.dumps(payload, ensure_ascii=False)
    required = {ord(c) for c in text if '\u3400' <= c <= '\u9fff'}
    for family in ('Noto Sans CJK SC', 'Noto Sans CJK JP', 'WenQuanYi Zen Hei', 'Droid Sans Fallback'):
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
        except ValueError:
            continue
        if required.issubset(font_manager.get_font(path).get_charmap()):
            return path
    raise ValueError('缺少可显示正文的中文字体；Linux可安装fonts-noto-cjk，不生成缺字图片')


def render(payload, output):
    workspace = workspace_path().resolve()
    output = output.resolve()
    research = workspace / 'research'
    if not output.is_relative_to(research) or output == research:
        raise ValueError('图表只写入workspace/research下的新目录')
    if output.exists() or any((p / 'research.json').exists() for p in [output, *output.parents] if p.is_relative_to(research)):
        raise ValueError('不能覆盖图表或已登记的冻结报告')
    verified = validate(payload, workspace)
    try:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib import pyplot as plt, font_manager
    except ImportError as exc:
        raise ValueError('制图需要research依赖：pip install -e "./valuation/backend[research]"') from exc
    font_path = chinese_font(payload)
    font = font_manager.FontProperties(fname=font_path)
    output.mkdir(parents=True)
    files, captions = [], []
    try:
        with plt.rc_context({'font.family': font.get_name(), 'axes.unicode_minus': False, 'font.size': 11,
                             'axes.spines.top': False, 'axes.spines.right': False}):
            for chart in payload['charts']:
                fig, ax = plt.subplots(figsize=(11, 6.4), layout='constrained')
                try:
                    labels, kind = chart['labels'], chart['kind']
                    x = list(range(len(labels)))
                    if kind in ('line', 'bar'):
                        width = 0.78 / len(chart['series'])
                        for i, series in enumerate(chart['series']):
                            values = [math.nan if v is None else v for v in series['values']]
                            role = {'reported': '已披露', 'forecast': '条件预测', 'reference': '参考/对照'}[series['role']]
                            label = series['label'] + '（' + role + '）'
                            color = COLORS[i % len(COLORS)]
                            if kind == 'line':
                                ax.plot(x, values, marker='o', color=color, label=label,
                                        linestyle='--' if series['role'] != 'reported' else '-')
                            else:
                                ax.bar([v + (i-(len(chart['series'])-1)/2)*width for v in x], values,
                                       width=width, color=color, label=label,
                                       hatch='//' if series['role'] == 'forecast' else None)
                        ax.legend(loc='best', fontsize=9)
                    elif kind == 'waterfall':
                        running = 0
                        for i, (value, typ) in enumerate(zip(chart['values'], chart['types'])):
                            bottom = 0 if typ == 'total' else min(running, running+value)
                            height = value if typ == 'total' else abs(value)
                            color = COLORS[0] if typ == 'total' else COLORS[2] if value >= 0 else '#b95752'
                            ax.bar(i, height, bottom=bottom, color=color, width=0.65)
                            running = value if typ == 'total' else running+value
                            ax.annotate(f'{value:,.2f}', (i, bottom+height), xytext=(0, 5),
                                        textcoords='offset points', ha='center', fontsize=9)
                    else:
                        im = ax.imshow(chart['values'], cmap='YlGnBu', aspect='auto')
                        fig.colorbar(im, ax=ax, label=chart['unit'])
                        ax.set_yticks(range(len(chart['row_labels'])), chart['row_labels'])
                        for y, row in enumerate(chart['values']):
                            for i, value in enumerate(row):
                                ax.text(i, y, f'{value:,.2f}', ha='center', va='center', fontsize=9,
                                        bbox={'facecolor': 'white', 'alpha': .8, 'edgecolor': 'none'})
                    ax.set_xticks(x, labels, rotation=20 if len(labels)>6 else 0, ha='right' if len(labels)>6 else 'center')
                    ax.set_title(payload['company']+'｜'+chart['title'], loc='left', fontsize=15, pad=18)
                    if kind != 'heatmap':
                        ax.set_ylabel(chart['unit'])
                        ax.axhline(0, color='#a3acb9', linewidth=.6)
                        ax.grid(axis='y', alpha=.15)
                        ax.set_axisbelow(True)
                    source_label = '；'.join(s['label'] for s in payload['sources'] if s['id'] in chart['source_ids'])
                    fig.supxlabel(chart['period']+'｜来源：'+source_label, fontsize=9, color='#505b69')
                    path = output / (chart['id']+'.png')
                    fig.savefig(path, dpi=180, facecolor='white')
                finally:
                    plt.close(fig)
                files.append(dict(id=chart['id'], label=chart['title'], path=path.relative_to(workspace).as_posix(),
                                  sha256=digest(path), media_type='image/png'))
                captions += ['### '+chart['title'], '', f"![{chart['title']}]({path.name})", '', chart['takeaway'], '',
                             '口径：'+chart['period']+'；单位：'+chart['unit']+'。', '',
                             '来源：'+'、'.join('['+s['label']+']('+Path(os.path.relpath(verified[s['id']], output)).as_posix()+')'
                                             for s in payload['sources'] if s['id'] in chart['source_ids'])+
                             '；逐项定位及绘图数据见[数据文件](chart-data.json)。', '']
            for source in payload['sources']:
                if digest(verified[source['id']]) != source['sha256']:
                    raise ValueError('制图期间来源变化')
        (output/'chart-data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        (output/'gallery.md').write_text('\n'.join(captions))
        manifest = dict(contract=CONTRACT, company=payload['company'], files=files,
                        dataset_sha256=digest(output/'chart-data.json'), font=font.get_name(),
                        renderer_version=matplotlib.__version__, model_calculated=False,
                        boundary='仅展示给定数据；哈希不证明金额、口径或研究判断已独立核验')
        (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
        return manifest
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = render(json.loads(args.input.read_text()), args.output)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
