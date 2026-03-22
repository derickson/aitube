import React from "react";

interface Tab {
  id: string;
  label: string;
}

interface ContentTabsProps {
  tabs: Tab[];
  activeTab: string;
  onTabChange: (tab: string) => void;
  children: React.ReactNode;
}

export function ContentTabs({ tabs, activeTab, onTabChange, children }: ContentTabsProps) {
  return (
    <>
      <div className="content-tabs-bar">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`content-tab-btn${activeTab === tab.id ? " active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {children}
    </>
  );
}
