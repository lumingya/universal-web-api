// R2-8：预编译 Tailwind 的配置，与 static/index.html 原先内联给运行时（Play CDN 3.4.17）的配置保持一致。
// 中性色（gray/slate/stone）读取 CSS 变量：亮色下与 Tailwind 默认值一致，夜间由 dashboard-dark.css 统一收敛到暖绿色板。
// 修改模板里的类名后请重新生成：python scripts/build_tailwind.py（需要 Node.js）。
const shades = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const scale = (name) => Object.fromEntries(shades.map((s) => [s, `rgb(var(--twc-${name}-${s}) / <alpha-value>)`]));

module.exports = {
  darkMode: 'class',
  content: ['./static/index.html', './static/js/**/*.js'],
  theme: {
    extend: {
      colors: {
        gray: scale('gray'),
        slate: scale('slate'),
        stone: scale('stone'),
      },
    },
  },
};
