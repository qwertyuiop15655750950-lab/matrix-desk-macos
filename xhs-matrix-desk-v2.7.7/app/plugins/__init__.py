"""采集通道兼容路由；生产采集不依赖外部两个项目。"""
def resolve_comments(requested):
    if requested not in ('plugin','builtin','bb-browser','crawl4ai'):
        raise ValueError('采集通道无效')
    legacy=requested in ('bb-browser','crawl4ai')
    return {'requested_engine':requested, 'engine':'builtin' if legacy else requested,
            'degraded':legacy, 'warning':'旧外部工具通道已改为自有浏览器采集器。' if legacy else ''}

def collection_metadata(result, selection):
    return {'requested_engine':selection['requested_engine'], 'engine':selection['engine'],
            'degraded':selection['degraded'],
            'blocked':bool(result.get('blocked')), 'requires_review':bool(result.get('requires_review')),
            'warning':' '.join(value for value in [selection['warning'],str(result.get('warning') or ''),
                       str(result.get('error') or '') if result.get('blocked') else ''] if value)}
