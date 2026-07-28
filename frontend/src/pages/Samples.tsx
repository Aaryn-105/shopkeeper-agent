import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { api, ApiError } from "../lib/api";
import type { ConfigPayload } from "../lib/types";

export function SamplesPage() {
  const [config, setConfig] = useState<ConfigPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e: unknown) =>
        setErr(e instanceof ApiError ? `${e.status} ${e.message}` : String(e)),
      );
  }, []);

  if (err) {
    return (
      <Card title="加载失败">
        <p className="text-sm text-rose-700">{err}</p>
      </Card>
    );
  }
  if (!config) {
    return <p className="text-sm text-slate-400">加载中...</p>;
  }

  // group samples by category for a tidier page
  const byCategory = new Map<string, ConfigPayload["samples"]>();
  for (const s of config.samples) {
    const arr = byCategory.get(s.category) ?? [];
    arr.push(s);
    byCategory.set(s.category, arr);
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-slate-900">示例问题</h1>
        <p className="text-sm text-slate-500">
          点击任一问题即可前往提问页，并把内容填入输入框。
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {[...byCategory.entries()].map(([cat, items]) => (
          <Card key={cat} title={cat}>
            <ul className="space-y-2">
              {items.map((s) => (
                <li key={s.id}>
                  <Link
                    to={`/?q=${encodeURIComponent(s.question)}`}
                    state={{ query: s.question }}
                    className="block rounded-lg border border-slate-200 px-3 py-2 hover:border-slate-400 hover:bg-slate-50"
                  >
                    <p className="text-sm font-medium text-slate-800">
                      {s.question}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {s.description}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>

      {config.ui.usage_tips?.length > 0 && (
        <Card title="使用提示">
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
            {config.ui.usage_tips.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}