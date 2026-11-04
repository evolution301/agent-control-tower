/**
 * dsh-tower client: a "Tower" section in the settings sidebar.
 *
 * Hand-written in the harness' client module format (no bundler needed): the
 * factory receives the host's `require`, registers one slot entry on
 * `settings.section`, and renders the local control panel in an iframe. The host
 * plugin (`index.js`) starts `tower panel` on mount, so the section works as soon
 * as the bundle is installed.
 */
window.__ModuleLoader__.load({
  id: 'dsh-tower',
  factory: (require) => {
    const module = { exports: {} }
    const exports = module.exports
    const React = require('react')

    const DEFAULT_PANEL_URL = 'http://127.0.0.1:8787/'

    function towerPanelUrl(injected) {
      const fromHost = injected && typeof injected.panelUrl === 'string' ? injected.panelUrl : ''
      return fromHost || DEFAULT_PANEL_URL
    }

    function TowerSection(props) {
      const base = towerPanelUrl(props)
      const [nonce, setNonce] = React.useState(0)
      const separator = base.indexOf('?') >= 0 ? '&' : '?'
      const src = base + separator + 'refresh=' + nonce
      const box = {
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        height: '100%',
        boxSizing: 'border-box',
      }
      const row = { display: 'flex', alignItems: 'center', gap: '8px' }
      const muted = { color: 'var(--dsh-text-3, #888)', fontSize: '12px' }
      return React.createElement(
        'div',
        { style: box },
        React.createElement(
          'div',
          { style: row },
          React.createElement('strong', null, 'Tower'),
          React.createElement('span', { style: muted }, base),
          React.createElement('span', { style: { flex: 1 } }),
          React.createElement('button', { type: 'button', onClick: () => setNonce((n) => n + 1) }, '刷新'),
          React.createElement(
            'button',
            { type: 'button', onClick: () => window.open(base, '_blank', 'noopener') },
            '新窗口打开',
          ),
        ),
        React.createElement('iframe', {
          key: nonce,
          src,
          title: 'control tower panel',
          style: {
            flex: 1,
            minHeight: '520px',
            border: '1px solid var(--dsh-border, rgba(127,127,127,.25))',
            borderRadius: '8px',
            background: 'var(--dsh-bg, #111)',
          },
        }),
        React.createElement(
          'div',
          { style: muted },
          '空白说明面板进程没起来：在项目目录运行 `tower panel`，或检查插件配置里的 panelPort。',
        ),
      )
    }

    function apply(ctx) {
      const disposers = []
      try {
        disposers.push(
          ctx.slots.inject('settings.section', () =>
            ctx.slots.register(
              {
                name: 'settings.section',
                id: 'tower',
                order: 120,
                label: () => 'Tower',
                inject: () => ({}),
              },
              TowerSection,
            ),
          ),
        )
      } catch (error) {
        console.error('[dsh-tower] settings section failed:', error)
      }
      return () => {
        for (const dispose of disposers) {
          if (typeof dispose === 'function') dispose()
        }
      }
    }

    exports.apply = apply
    // Deliberately no `inject`: declaring a service whose id never resolves keeps
    // this entry pending, and the whole web boot then fails with
    // "web boot: 1 entry did not activate". The slot service is read defensively.
    return exports
  },
})
