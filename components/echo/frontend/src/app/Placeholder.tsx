import { FileText, Headphones, Link2, Video } from "lucide-react";
import { useOutletContext } from "react-router-dom";
export function Placeholder() {
  const { openImport } = useOutletContext<{ openImport: () => void }>();
  return (
    <div className="placeholder">
      <section className="onboarding">
        <div className="onboarding-mark">
          <Link2 size={22} />
        </div>
        <h2>把链接变成可阅读的内容</h2>
        <p>
          收集网页文章、视频和播客，在安静的阅读空间里查看正文、转录和媒体。
        </p>
        <button type="button" onClick={openImport}>
          添加内容
        </button>
        <div className="onboarding-types">
          <span>
            <FileText size={15} />
            文章
          </span>
          <span>
            <Video size={15} />
            视频
          </span>
          <span>
            <Headphones size={15} />
            音频
          </span>
        </div>
      </section>
    </div>
  );
}
