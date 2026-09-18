/** @type {import('next').NextConfig} */

// 后端地址：开发默认本机 8001；生产用 NEXT_PUBLIC_API_BASE 显式指定。
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8001";

const nextConfig = {
  reactStrictMode: true,
  // 非流式接口走 rewrites 代理，省去前端跨域配置；
  // 注意 SSE 流式**不走这里**——Next 代理会缓冲整个响应，流式会退化成
  // "整段一次显示"，所以 lib/api.ts 里的流式请求直连 API_BASE（见开发文档 §3.2）。
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_BASE}/api/:path*` }];
  },
};

export default nextConfig;
