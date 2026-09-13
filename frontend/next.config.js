/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  transpilePackages: ['reactflow'],
  // 构建产物目录可切换，默认不变。
  //
  // 为什么需要：`next dev` 和 `next build` 默认都写 `.next`。dev server 跑着的时候
  // 执行一次 `next build`，会把它的 dev-mode chunk 全换成生产 chunk —— dev server
  // 内存里的 manifest 还指着已经没了的文件，于是**页面 HTML 照常返回、所有 chunk
  // 404、React 完全不 hydrate**（看起来像代码坏了，其实只是缓存被覆盖）。
  // 跑构建时带上 NEXT_DIST_DIR=.next-build 即可与 dev server 互不干扰。
  distDir: process.env.NEXT_DIST_DIR || '.next',
};

module.exports = nextConfig;
