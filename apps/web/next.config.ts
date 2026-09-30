import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker 이미지에 필요한 파일만 담기 위해 standalone 출력 사용
  output: "standalone",
};

export default nextConfig;
