import React, { useState, useMemo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  Table as TableIcon,
  BarChart3,
  TrendingUp,
  Activity,
  Copy,
  Check,
  Download,
  ArrowUpDown,
  Search,
} from "lucide-react";
import { ColorTheme } from "../types";
import { COLOR_THEMES } from "../data/presets";
import { sfx } from "../utils/audio";

export interface ParsedTableData {
  title?: string;
  headers: string[];
  rows: string[][];
  rawMarkdown: string;
}

interface DataVisualizationCardProps {
  tableData: ParsedTableData;
  colorTheme?: ColorTheme;
  isDark?: boolean;
}

type TabType = "table" | "bar" | "line" | "metrics";

interface NumericColumnMeta {
  index: number;
  header: string;
  values: number[];
  unit: string;
  min: number;
  max: number;
  avg: number;
  sum: number;
}

export const DataVisualizationCard: React.FC<DataVisualizationCardProps> = ({
  tableData,
  colorTheme = "beams",
  isDark = true,
}) => {
  const theme = COLOR_THEMES[colorTheme] || COLOR_THEMES.beams || COLOR_THEMES.violet;
  const [copied, setCopied] = useState(false);
  const [filterQuery, setFilterQuery] = useState("");
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortAsc, setSortAsc] = useState<boolean>(true);
  const [selectedNumColIdx, setSelectedNumColIdx] = useState<number>(0);

  // Analyze columns to detect numbers and extract statistics
  const { numericCols, labelColIndex } = useMemo(() => {
    const { headers, rows } = tableData;
    if (!headers.length || !rows.length) {
      return { numericCols: [], labelColIndex: 0 };
    }

    const detected: NumericColumnMeta[] = [];
    let detectedLabelCol = 0;

    headers.forEach((header, colIdx) => {
      let numericCount = 0;
      const values: number[] = [];
      let detectedUnit = "";

      rows.forEach((row) => {
        const cell = row[colIdx] || "";
        // Match numbers, allowing currencies ($ € £ ₹ ¥), % signs, commas, and suffixes (k, m, etc.)
        const unitMatch = cell.match(/([$€£₹¥%]|ms|fps|gb|mb|kb|k|m)/i);
        if (unitMatch && !detectedUnit) {
          detectedUnit = unitMatch[0];
        }

        const cleaned = cell
          .replace(/[$€£₹¥,]/g, "")
          .replace(/(?:ms|fps|gb|mb|kb|%)/gi, "")
          .trim();

        const num = parseFloat(cleaned);
        if (!isNaN(num) && isFinite(num)) {
          numericCount++;
          values.push(num);
        } else {
          values.push(0);
        }
      });

      // If at least 60% of non-empty rows in this column are numeric, treat as numeric column
      if (numericCount >= Math.max(1, Math.floor(rows.length * 0.6))) {
        const validValues = values.filter((_, idx) => (rows[idx][colIdx] || "").trim() !== "");
        const min = validValues.length ? Math.min(...validValues) : 0;
        const max = validValues.length ? Math.max(...validValues) : 100;
        const sum = validValues.reduce((acc, v) => acc + v, 0);
        const avg = validValues.length ? sum / validValues.length : 0;

        detected.push({
          index: colIdx,
          header,
          values,
          unit: detectedUnit,
          min,
          max: max === min ? min + 1 : max,
          avg,
          sum,
        });
      }
    });

    // Detect primary text label column (first non-numeric column)
    for (let c = 0; c < headers.length; c++) {
      if (!detected.some((nc) => nc.index === c)) {
        detectedLabelCol = c;
        break;
      }
    }

    return { numericCols: detected, labelColIndex: detectedLabelCol };
  }, [tableData]);

  const hasGraphs = numericCols.length > 0;
  const [activeTab, setActiveTab] = useState<TabType>("table");

  const currentNumCol = numericCols[selectedNumColIdx] || numericCols[0];

  // Sorting and filtering table rows
  const displayRows = useMemo(() => {
    let rows = [...tableData.rows];

    if (filterQuery.trim()) {
      const q = filterQuery.toLowerCase();
      rows = rows.filter((r) => r.some((cell) => cell.toLowerCase().includes(q)));
    }

    if (sortCol !== null) {
      rows.sort((a, b) => {
        const valA = (a[sortCol] || "").trim();
        const valB = (b[sortCol] || "").trim();
        const numA = parseFloat(valA.replace(/[^0-9.-]+/g, ""));
        const numB = parseFloat(valB.replace(/[^0-9.-]+/g, ""));

        if (!isNaN(numA) && !isNaN(numB)) {
          return sortAsc ? numA - numB : numB - numA;
        }
        return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
      });
    }

    return rows;
  }, [tableData.rows, filterQuery, sortCol, sortAsc]);

  const handleCopyTable = () => {
    sfx.playClick?.();
    const tsv = [
      tableData.headers.join("\t"),
      ...tableData.rows.map((r) => r.join("\t")),
    ].join("\n");
    navigator.clipboard.writeText(tsv);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadCSV = () => {
    sfx.playClick?.();
    const csvContent =
      "data:text/csv;charset=utf-8," +
      [
        tableData.headers.map((h) => `"${h.replace(/"/g, '""')}"`).join(","),
        ...tableData.rows.map((r) =>
          r.map((c) => `"${(c || "").replace(/"/g, '""')}"`).join(",")
        ),
      ].join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `${tableData.title || "amigo_data"}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div
      className="my-3.5 rounded-2xl border transition-all duration-300 overflow-hidden shadow-xl bg-zinc-950/95 border-zinc-800 text-slate-100 shadow-black/70"
    >
      {/* Header Bar with Title, View Mode Tabs, and Actions */}
      <div
        className="px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900/60"
      >
        <div className="flex items-center space-x-2 min-w-0">
          <div
            className="w-2 h-2 rounded-full animate-pulse shrink-0"
            style={{ backgroundColor: theme.primary }}
          />
          <h4 className="text-xs font-semibold tracking-wide uppercase truncate opacity-90">
            {tableData.title || "Dataset & Visualization"}
          </h4>
        </div>

        {/* Tab Controls (Table vs Bar Chart vs Trend Line vs Metrics) */}
        <div className="flex items-center space-x-1">
          {hasGraphs && (
            <div
              className={`flex items-center p-0.5 rounded-xl border text-[11px] font-medium ${
                isDark ? "bg-black/30 border-white/[0.08]" : "bg-black/[0.04] border-black/[0.06]"
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  sfx.playClick?.();
                  setActiveTab("table");
                }}
                className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg transition-all ${
                  activeTab === "table"
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "opacity-60 hover:opacity-100"
                }`}
                style={activeTab === "table" ? { backgroundColor: theme.primary } : {}}
              >
                <TableIcon className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Table</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  sfx.playClick?.();
                  setActiveTab("bar");
                }}
                className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg transition-all ${
                  activeTab === "bar"
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "opacity-60 hover:opacity-100"
                }`}
                style={activeTab === "bar" ? { backgroundColor: theme.primary } : {}}
              >
                <BarChart3 className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Bar Chart</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  sfx.playClick?.();
                  setActiveTab("line");
                }}
                className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg transition-all ${
                  activeTab === "line"
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "opacity-60 hover:opacity-100"
                }`}
                style={activeTab === "line" ? { backgroundColor: theme.primary } : {}}
              >
                <TrendingUp className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Trend</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  sfx.playClick?.();
                  setActiveTab("metrics");
                }}
                className={`flex items-center space-x-1 px-2.5 py-1 rounded-lg transition-all ${
                  activeTab === "metrics"
                    ? "bg-white/15 text-white font-semibold shadow-sm"
                    : "opacity-60 hover:opacity-100"
                }`}
                style={activeTab === "metrics" ? { backgroundColor: theme.primary } : {}}
              >
                <Activity className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Stats</span>
              </button>
            </div>
          )}

          {/* Export Actions */}
          <div className="flex items-center space-x-1 ml-1.5">
            <button
              type="button"
              onClick={handleCopyTable}
              className={`p-1.5 rounded-lg text-xs transition-colors border ${
                isDark
                  ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.08] text-slate-300"
                  : "bg-black/[0.04] hover:bg-black/[0.08] border-black/[0.06] text-slate-700"
              }`}
              title="Copy Table to Clipboard (TSV/Excel)"
            >
              {copied ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </button>
            <button
              type="button"
              onClick={handleDownloadCSV}
              className={`p-1.5 rounded-lg text-xs transition-colors border ${
                isDark
                  ? "bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.08] text-slate-300"
                  : "bg-black/[0.04] hover:bg-black/[0.08] border-black/[0.06] text-slate-700"
              }`}
              title="Download as CSV"
            >
              <Download className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Metric Column Selector (if more than 1 numeric column exists) */}
      {hasGraphs && numericCols.length > 1 && activeTab !== "table" && (
        <div
          className={`px-4 py-1.5 flex items-center space-x-2 border-b text-xs overflow-x-auto no-scrollbar ${
            isDark ? "border-white/[0.04] bg-white/[0.01]" : "border-black/[0.03] bg-black/[0.01]"
          }`}
        >
          <span className="opacity-60 text-[11px] shrink-0 font-medium">Metric:</span>
          {numericCols.map((col, idx) => (
            <button
              key={col.header}
              type="button"
              onClick={() => {
                sfx.playClick?.();
                setSelectedNumColIdx(idx);
              }}
              className={`px-2 py-0.5 rounded-md text-[11px] font-medium transition-all shrink-0 ${
                selectedNumColIdx === idx
                  ? "bg-white/20 text-white font-semibold"
                  : "opacity-60 hover:opacity-100"
              }`}
              style={selectedNumColIdx === idx ? { backgroundColor: `${theme.primary}50` } : {}}
            >
              {col.header} {col.unit ? `(${col.unit})` : ""}
            </button>
          ))}
        </div>
      )}

      {/* Main Visualizer Content Area */}
      <div className="p-4">
        <AnimatePresence mode="wait">
          {/* 1. Bar Chart View */}
          {activeTab === "bar" && currentNumCol && (
            <motion.div
              key="bar-chart"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-3"
            >
              <div className="flex justify-between items-center text-xs opacity-70 mb-1 px-1">
                <span>Category / Label</span>
                <span className="font-mono">
                  {currentNumCol.header} {currentNumCol.unit ? `(${currentNumCol.unit})` : ""}
                </span>
              </div>

              <div className="space-y-2.5">
                {tableData.rows.map((row, rIdx) => {
                  const label = row[labelColIndex] || `Item ${rIdx + 1}`;
                  const rawVal = row[currentNumCol.index] || "0";
                  const numVal = currentNumCol.values[rIdx] || 0;
                  const ratio = Math.max(0, Math.min(100, (numVal / (currentNumCol.max || 1)) * 100));

                  return (
                    <div key={rIdx} className="group flex flex-col space-y-1">
                      <div className="flex justify-between items-center text-xs font-medium">
                        <span className="truncate max-w-[65%] text-slate-300 group-hover:text-white transition-colors">
                          {label}
                        </span>
                        <span className="font-mono font-semibold text-slate-200">
                          {rawVal}
                        </span>
                      </div>
                      <div
                        className={`h-4 w-full rounded-full overflow-hidden p-0.5 border ${
                          isDark ? "bg-black/40 border-white/[0.06]" : "bg-black/[0.06] border-black/[0.08]"
                        }`}
                      >
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${Math.max(4, ratio)}%` }}
                          transition={{ duration: 0.5, delay: rIdx * 0.04, ease: "easeOut" }}
                          className="h-full rounded-full shadow-sm relative group-hover:brightness-125 transition-all"
                          style={{
                            background: theme.gradient,
                            boxShadow: `0 0 10px ${theme.glow}`,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </motion.div>
          )}

          {/* 2. Line / Trend View */}
          {activeTab === "line" && currentNumCol && (
            <motion.div
              key="line-chart"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex flex-col space-y-3"
            >
              {(() => {
                const width = 500;
                const height = 180;
                const padding = 28;
                const innerW = width - padding * 2;
                const innerH = height - padding * 2;
                const values = currentNumCol.values;
                const count = values.length;
                if (count < 2) {
                  return (
                    <div className="py-8 text-center text-xs opacity-60">
                      Need at least 2 data points for trend line.
                    </div>
                  );
                }

                const minVal = currentNumCol.min;
                const maxVal = currentNumCol.max;
                const valRange = maxVal - minVal || 1;

                const points = values.map((v, i) => {
                  const x = padding + (i / (count - 1)) * innerW;
                  const y = height - padding - ((v - minVal) / valRange) * innerH;
                  return { x, y, val: v, label: tableData.rows[i]?.[labelColIndex] || `${i + 1}` };
                });

                // Generate smooth SVG polyline / curve path
                const pathD = points.reduce((acc, p, i) => {
                  if (i === 0) return `M ${p.x} ${p.y}`;
                  const prev = points[i - 1];
                  const cpX = (prev.x + p.x) / 2;
                  return `${acc} C ${cpX} ${prev.y}, ${cpX} ${p.y}, ${p.x} ${p.y}`;
                }, "");

                const areaD = `${pathD} L ${points[points.length - 1].x} ${height - padding} L ${points[0].x} ${height - padding} Z`;

                return (
                  <div className="w-full overflow-hidden">
                    <svg
                      viewBox={`0 0 ${width} ${height}`}
                      className="w-full h-auto drop-shadow-md overflow-visible"
                    >
                      <defs>
                        <linearGradient id={`area-grad-${colorTheme}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={theme.primary} stopOpacity="0.45" />
                          <stop offset="100%" stopColor={theme.accent || theme.primary} stopOpacity="0.0" />
                        </linearGradient>
                      </defs>

                      {/* Horizontal Grid lines */}
                      {[0, 0.5, 1].map((ratio) => {
                        const y = height - padding - ratio * innerH;
                        return (
                          <line
                            key={ratio}
                            x1={padding}
                            y1={y}
                            x2={width - padding}
                            y2={y}
                            stroke={isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)"}
                            strokeDasharray="3 3"
                          />
                        );
                      })}

                      {/* Gradient Fill under curve */}
                      <path d={areaD} fill={`url(#area-grad-${colorTheme})`} />

                      {/* Smooth Trend Curve Line */}
                      <path
                        d={pathD}
                        fill="none"
                        stroke={theme.primary}
                        strokeWidth="3.5"
                        strokeLinecap="round"
                      />

                      {/* Plotted Node Points with Hover Tooltips */}
                      {points.map((p, idx) => (
                        <g key={idx} className="group cursor-pointer">
                          <circle
                            cx={p.x}
                            cy={p.y}
                            r="5"
                            fill={isDark ? "#0f172a" : "#ffffff"}
                            stroke={theme.primary}
                            strokeWidth="2.5"
                            className="transition-transform duration-150 group-hover:scale-125"
                          />
                          <title>{`${p.label}: ${p.val}${currentNumCol.unit}`}</title>
                        </g>
                      ))}
                    </svg>

                    {/* Labels along bottom */}
                    <div className="flex justify-between items-center text-[10px] opacity-60 font-mono mt-2 px-1">
                      <span className="truncate max-w-[30%]">{points[0]?.label}</span>
                      {points.length > 2 && (
                        <span className="truncate max-w-[30%] text-center">
                          {points[Math.floor(points.length / 2)]?.label}
                        </span>
                      )}
                      <span className="truncate max-w-[30%] text-right">
                        {points[points.length - 1]?.label}
                      </span>
                    </div>
                  </div>
                );
              })()}
            </motion.div>
          )}

          {/* 3. Summary Statistics Metrics View */}
          {activeTab === "metrics" && currentNumCol && (
            <motion.div
              key="metrics-summary"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="grid grid-cols-2 sm:grid-cols-4 gap-2.5"
            >
              <div
                className={`p-3 rounded-xl border flex flex-col justify-between ${
                  isDark ? "bg-white/[0.03] border-white/[0.08]" : "bg-black/[0.03] border-black/[0.06]"
                }`}
              >
                <span className="text-[11px] opacity-60 font-medium">Average</span>
                <span className="text-base sm:text-lg font-bold font-mono tracking-tight mt-1 text-slate-100">
                  {currentNumCol.avg.toFixed(1)} {currentNumCol.unit}
                </span>
              </div>

              <div
                className={`p-3 rounded-xl border flex flex-col justify-between ${
                  isDark ? "bg-white/[0.03] border-white/[0.08]" : "bg-black/[0.03] border-black/[0.06]"
                }`}
              >
                <span className="text-[11px] opacity-60 font-medium">Peak / High</span>
                <span className="text-base sm:text-lg font-bold font-mono tracking-tight mt-1 text-emerald-400">
                  {currentNumCol.max.toFixed(1)} {currentNumCol.unit}
                </span>
              </div>

              <div
                className={`p-3 rounded-xl border flex flex-col justify-between ${
                  isDark ? "bg-white/[0.03] border-white/[0.08]" : "bg-black/[0.03] border-black/[0.06]"
                }`}
              >
                <span className="text-[11px] opacity-60 font-medium">Floor / Low</span>
                <span className="text-base sm:text-lg font-bold font-mono tracking-tight mt-1 text-amber-400">
                  {currentNumCol.min.toFixed(1)} {currentNumCol.unit}
                </span>
              </div>

              <div
                className={`p-3 rounded-xl border flex flex-col justify-between ${
                  isDark ? "bg-white/[0.03] border-white/[0.08]" : "bg-black/[0.03] border-black/[0.06]"
                }`}
              >
                <span className="text-[11px] opacity-60 font-medium">Total Sum</span>
                <span className="text-base sm:text-lg font-bold font-mono tracking-tight mt-1 text-sky-400">
                  {currentNumCol.sum.toFixed(1)} {currentNumCol.unit}
                </span>
              </div>
            </motion.div>
          )}

          {/* 4. Rich Table View */}
          {activeTab === "table" && (
            <motion.div
              key="table-view"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-2.5"
            >
              {/* Optional Table Search Filter */}
              {tableData.rows.length > 5 && (
                <div className="relative mb-2">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 opacity-40 pointer-events-none" />
                  <input
                    type="text"
                    value={filterQuery}
                    onChange={(e) => setFilterQuery(e.target.value)}
                    placeholder="Filter rows..."
                    className={`w-full pl-8 pr-3 py-1.5 rounded-lg text-xs outline-none border transition-colors ${
                      isDark
                        ? "bg-black/30 border-white/[0.08] focus:border-white/20 text-white placeholder-slate-500"
                        : "bg-black/[0.03] border-black/[0.08] focus:border-black/20 text-black placeholder-slate-400"
                    }`}
                  />
                </div>
              )}

              <div className="overflow-x-auto rounded-xl border border-white/[0.06] no-scrollbar">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr
                      className={`${
                        isDark ? "bg-white/[0.04] text-slate-200" : "bg-black/[0.04] text-slate-700"
                      } border-b border-white/[0.06]`}
                    >
                      {tableData.headers.map((h, i) => (
                        <th
                          key={i}
                          onClick={() => {
                            if (sortCol === i) {
                              setSortAsc(!sortAsc);
                            } else {
                              setSortCol(i);
                              setSortAsc(true);
                            }
                          }}
                          className="px-3.5 py-2.5 font-semibold tracking-wide cursor-pointer hover:opacity-80 select-none whitespace-nowrap"
                        >
                          <div className="flex items-center space-x-1.5">
                            <span>{h}</span>
                            <ArrowUpDown className="w-3 h-3 opacity-40" />
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {displayRows.length === 0 ? (
                      <tr>
                        <td
                          colSpan={tableData.headers.length}
                          className="px-4 py-6 text-center text-xs opacity-50 font-mono"
                        >
                          No matching records found.
                        </td>
                      </tr>
                    ) : (
                      displayRows.map((row, rIdx) => (
                        <tr
                          key={rIdx}
                          className={`transition-colors border-b border-white/[0.04] ${
                            rIdx % 2 === 0
                              ? "bg-transparent"
                              : isDark
                              ? "bg-white/[0.015]"
                              : "bg-black/[0.015]"
                          } ${isDark ? "hover:bg-white/[0.04]" : "hover:bg-black/[0.04]"}`}
                        >
                          {row.map((cell, cIdx) => (
                            <td
                              key={cIdx}
                              className="px-3.5 py-2 text-slate-300 whitespace-nowrap font-normal"
                            >
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};
