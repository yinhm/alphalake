"""把已核对的研究数据绘成中文PNG，不计算估值、选择假设或下载数据。"""
import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import unicodedata

from data_sources.paths import workspace_path

CONTRACT = 'alphalake-research-charts-v1'
COLORS = ['#28556b', '#319488', '#c18b45', '#7c779b', '#718292']
INK, MUTED, GRID, NEGATIVE = '#223746', '#657785', '#e8eef1', '#b66b61'
STYLE = 'research-editorial-v2'
ROLES = {'reported': '已披露', 'forecast': '条件预测', 'reference': '参考对照'}


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
        if kind in ('line', 'bar', 'dot'):
            if not chart.get('series'):
                raise ValueError('图表序列 required')
            for series in chart['series']:
                if (not series.get('label') or series.get('role') not in ROLES
                        or len(series['values']) != len(labels)):
                    raise ValueError('序列必须标明名称、事实/预测/参考角色及完整标签长度')
                for value in series['values']:
                    numeric(value, nullable=True)
                if not any(v is not None for v in series['values']):
                    raise ValueError('全空序列不能绘成零线')
            if kind == 'bar' and chart.get('orientation', 'vertical') not in ('vertical', 'horizontal'):
                raise ValueError('柱图方向只能是vertical或horizontal')
            if kind == 'dot':
                if len(chart['series']) != 1:
                    raise ValueError('点图仅接受一组已计算对照')
                reference = chart.get('reference', {})
                numeric(reference.get('value'))
                if not isinstance(reference.get('label'), str) or not reference['label'].strip():
                    raise ValueError('点图必须显式提供已有基准及标签')
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
            raise ValueError('只支持line/bar/dot/waterfall/heatmap')
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


def wrapped(text, width):
    """按显示宽度折行，中文标签不靠旋转或缩成小字塞进图中。"""
    lines, line, used = [], '', 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if char == '\n' or used + size > width:
            lines.append(line)
            line, used = '', 0
        if char != '\n':
            line += char
            used += size
    return '\n'.join([*lines, line])


def number(value):
    return f'{value:,.2f}'


def clean_axis(ax, horizontal=False):
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='both', length=0, pad=10, labelcolor=MUTED)
    axis = ax.xaxis if horizontal else ax.yaxis
    axis.set_major_locator(MaxNLocator(nbins=4))
    axis.set_major_formatter(FuncFormatter(lambda value, _: number(value).rstrip('0').rstrip('.')))
    ax.grid(axis='x' if horizontal else 'y', color=GRID, linewidth=.8)
    ax.set_axisbelow(True)


def bars(ax, labels, series, horizontal=False):
    x = list(range(len(labels)))
    width = .58 / len(series)
    visible = [v for s in series for v in s['values'] if v is not None]
    for i, item in enumerate(series):
        positions = [v + (i-(len(series)-1)/2)*width for v in x]
        values = [math.nan if v is None else v for v in item['values']]
        draw = ax.barh if horizontal else ax.bar
        containers = draw(positions, values, **({'height': width*.82} if horizontal else {'width': width*.82}),
                          color=COLORS[i % len(COLORS)], label=item['label'], zorder=3)
        if len(labels)*len(series) <= 16:
            for patch, value in zip(containers, item['values']):
                if value is None:
                    continue
                xy = (value, patch.get_y()+patch.get_height()/2) if horizontal else (patch.get_x()+patch.get_width()/2, value)
                ax.annotate(number(value), xy, xytext=((7 if value >= 0 else -7), 0) if horizontal else (0, 7 if value >= 0 else -7),
                            textcoords='offset points', ha=('left' if value >= 0 else 'right') if horizontal else 'center',
                            va='center' if horizontal else 'bottom' if value >= 0 else 'top', color=INK, fontsize=11)
    clean_axis(ax, horizontal)
    low, high = min(0, min(visible)), max(0, max(visible))
    span = high-low or 1
    limits = (low-(.18*span if low < 0 else 0), high+.2*span)
    if horizontal:
        ax.set_yticks(x, [wrapped(s, 24) for s in labels], color=INK)
        ax.set_ylim(len(labels)-.45, -.55)
        ax.set_xlim(*limits)
        ax.axvline(0, color=GRID, linewidth=1)
    else:
        ax.set_xticks(x, [wrapped(s, 14) for s in labels])
        ax.set_ylim(*limits)
        ax.set_xlim(-.6, len(labels)-.4)


def draw_chart(fig, chart):
    """一套报告版式；图型由输入明确指定，不按公司或估值结果选图。"""
    kind, labels = chart['kind'], chart['labels']
    series = chart.get('series', []) if kind in ('line', 'bar', 'dot') else []
    has_legend = len(series) > 1
    horizontal = kind == 'dot' or kind == 'bar' and chart.get('orientation') == 'horizontal'
    left = .22 if horizontal else .08
    ax = fig.add_axes([left, .23, .94-left, .44 if has_legend else .50])
    axes = [ax]
    if kind == 'bar':
        bars(ax, labels, series, horizontal)
    elif kind == 'line':
        x = list(range(len(labels)))
        ends, values = [], []
        for i, item in enumerate(series):
            ys = [math.nan if v is None else v for v in item['values']]
            values += [v for v in item['values'] if v is not None]
            color = COLORS[i % len(COLORS)]
            ax.plot(x, ys, color=color, linewidth=2.4, marker='o', markersize=4.5,
                    markeredgecolor='white', markeredgewidth=.7, label=item['label'],
                    linestyle='--' if item['role'] != 'reported' else '-', zorder=3)
            if item['values'][-1] is not None:
                ends.append((item['values'][-1], color))
        clean_axis(ax)
        low, high = min(0, min(values)), max(0, max(values))
        span = high-low or 1
        ax.set_ylim(low-.08*span if low < 0 else 0, high+.17*span)
        ax.set_xlim(-.15, max(1, len(labels)-1)+.85)
        ax.set_xticks(x, [wrapped(s, 12) for s in labels])
        # 相近终点错开数字，用短引线保持它们与真实点的关系。
        previous = -math.inf
        for value, color in sorted(ends):
            y = max(value, previous+.08*span)
            previous = y
            ax.annotate(number(value), (len(labels)-1, value), xytext=(len(labels)-1+.22, y),
                        color=color, fontsize=11, va='center',
                        arrowprops=dict(arrowstyle='-', color=color, linewidth=.7))
        if ends:
            ax.set_ylim(top=max(ax.get_ylim()[1], previous+.08*span))
    elif kind == 'dot':
        base = chart['reference']['value']
        values = series[0]['values']
        for i, value in enumerate(values):
            if value is not None:
                ax.plot([base, value], [i, i], color='#c5d4dd', linewidth=2, zorder=2)
                ax.scatter(value, i, s=65, color=COLORS[0], edgecolor='white', linewidth=1, zorder=4)
                ax.annotate(number(value), (value, i), xytext=(9 if value >= base else -9, 0),
                            textcoords='offset points', ha='left' if value >= base else 'right',
                            va='center', color=INK, fontsize=11)
        clean_axis(ax, True)
        ax.axvline(base, color=MUTED, linewidth=1, linestyle=(0, (3, 3)))
        ax.text(base, 1.03, chart['reference']['label'], ha='center', transform=ax.get_xaxis_transform(),
                fontsize=10, color=MUTED)
        ax.set_yticks(range(len(labels)), [wrapped(s, 24) for s in labels], color=INK)
        ax.set_ylim(len(labels)-.5, -.5)
        extent = [base, *[v for v in values if v is not None]]
        low, high = min(extent), max(extent)
        span = high-low or max(abs(base)*.1, 1)
        ax.set_xlim(low-.12*span, high+.24*span)
    elif kind == 'waterfall':
        # 总值和小额调整分面，各自从零起，避免悬浮细条消失或截轴夸大差异。
        ax.set_position([.08, .23, .32, .45])
        changes = fig.add_axes([.63, .23, .29, .45])
        axes.append(changes)
        totals = [(s, v) for s, v, t in zip(labels, chart['values'], chart['types']) if t == 'total']
        deltas = [(s, v) for s, v, t in zip(labels, chart['values'], chart['types']) if t == 'change']
        bars(ax, [s for s, _ in totals], [dict(label='总值', values=[v for _, v in totals])])
        ax.set_title('经营与股权总值', loc='left', color=MUTED, fontsize=11, pad=18)
        clean_axis(changes, True)
        if deltas:
            for i, (label, value) in enumerate(deltas):
                changes.barh(i, value, height=.42, color=COLORS[1] if value >= 0 else NEGATIVE, zorder=3)
                changes.annotate(('+' if value > 0 else '')+number(value), (value, i),
                                 xytext=(7 if value >= 0 else -7, 0), textcoords='offset points',
                                 va='center', ha='left' if value >= 0 else 'right', fontsize=11, color=INK)
            bound = max(abs(v) for _, v in deltas) or 1
            changes.set_xlim(-bound*1.55, bound*1.55)
            changes.set_yticks(range(len(deltas)), [wrapped(s, 16) for s, _ in deltas], color=INK)
            changes.set_ylim(len(deltas)-.5, -.5)
        else:
            changes.text(.5, .5, '无调整项目', transform=changes.transAxes, ha='center', color=MUTED)
            changes.set_yticks([])
        changes.axvline(0, color='#a5b7c2', linewidth=.8)
        changes.set_title('加回与扣除 · 独立刻度', loc='left', color=MUTED, fontsize=11, pad=18)
    else:
        from matplotlib.colors import LinearSegmentedColormap
        cmap = LinearSegmentedColormap.from_list('research', ['#edf4f5', '#86b8ba', '#28556b'])
        im = ax.imshow(chart['values'], cmap=cmap, aspect='auto')
        ax.set_yticks(range(len(chart['row_labels'])), [wrapped(s, 20) for s in chart['row_labels']])
        ax.set_xticks(range(len(labels)), [wrapped(s, 14) for s in labels])
        for y, row in enumerate(chart['values']):
            for i, value in enumerate(row):
                ax.text(i, y, number(value), ha='center', va='center', fontsize=12,
                        color='white' if im.norm(value) > .65 else INK)
        ax.set_xticks([i-.5 for i in range(len(labels)+1)], minor=True)
        ax.set_yticks([i-.5 for i in range(len(chart['row_labels'])+1)], minor=True)
        ax.grid(which='minor', color='white', linewidth=3)
        ax.tick_params(which='both', length=0, pad=12, colors=MUTED)
        for spine in ax.spines.values():
            spine.set_visible(False)
    if has_legend:
        handles, names = ax.get_legend_handles_labels()
        if len({s['role'] for s in series}) > 1:
            names = [name+' · '+ROLES[s['role']] for name, s in zip(names, series)]
        fig.legend(handles, names, frameon=False, ncol=min(3, len(names)), loc='upper left',
                   bbox_to_anchor=(.065, .745), fontsize=11, handlelength=2, columnspacing=2)
    return axes


def render(payload, output):
    workspace = workspace_path().resolve()
    output = output.resolve()
    research = workspace / 'research'
    if not output.is_relative_to(research) or output == research:
        raise ValueError('图表只写入workspace/research下的新目录')
    if output.exists() or any((p / 'research.json').exists() for p in [output, *output.parents] if p.is_relative_to(research)):
        raise ValueError('不能覆盖图表或已登记的冻结报告')
    verified = validate(payload, workspace)
    # pyplot.close解除登记后仍有循环引用，及时回收前一份公司的图形及像素缓冲。
    gc.collect()
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
        with plt.rc_context({'font.family': font.get_name(), 'axes.unicode_minus': False,
                             'font.size': 11, 'text.color': INK, 'axes.labelcolor': MUTED}):
            for chart in payload['charts']:
                fig = plt.figure(figsize=(11.2, 6.4), facecolor='white')
                try:
                    fig.text(.065, .945, 'ALPHALAKE  /  '+payload['company'], fontsize=10, color=MUTED)
                    fig.text(.065, .89, wrapped(chart['title'], 64), fontsize=18, va='top', linespacing=1.35)
                    roles = list(dict.fromkeys(s['role'] for s in chart.get('series', [])))
                    meta = chart['unit'] + ('  ·  '+' / '.join(ROLES[r] for r in roles) if roles else '')
                    fig.text(.065, .775, meta, fontsize=10, color=MUTED)
                    draw_chart(fig, chart)
                    source_label = '；'.join(s['label'] for s in payload['sources'] if s['id'] in chart['source_ids'])
                    fig.text(.065, .105, wrapped('期间与口径 · '+chart['period'], 126),
                             fontsize=9, color=MUTED, va='top', linespacing=1.4)
                    fig.text(.065, .045, wrapped('来源 · '+source_label, 126), fontsize=9, color=MUTED, va='top')
                    # 指定实际字体文件，避免中文字体族的权重回退改变版式。
                    from matplotlib.text import Text
                    for text in fig.findobj(Text):
                        text.set_fontproperties(font_manager.FontProperties(fname=font_path, size=text.get_fontsize()))
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
                        renderer_version=matplotlib.__version__, style=STYLE, model_calculated=False,
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
