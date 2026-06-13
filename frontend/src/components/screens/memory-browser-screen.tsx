import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getGatewaySubagentReports } from "@/lib/api/tokoboros.functions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const MEMORY_QUERY_KEY = (q: string) => ["gateway", "memory-search", q] as const;

type MemoryEntry = {
  role?: string;
  status?: string;
  summary?: string;
  [key: string]: unknown;
};

function MemoryCard({ entry }: { entry: MemoryEntry }) {
  const role = String(entry.role ?? "agent");
  const summary = String(entry.summary ?? JSON.stringify(entry, null, 2));
  const status = String(entry.status ?? "");

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="text-sm font-medium capitalize">{role}</CardTitle>
          {status && <Badge variant="outline" className="text-xs">{status}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <p className="text-sm text-muted-foreground leading-relaxed line-clamp-4">{summary}</p>
      </CardContent>
    </Card>
  );
}

export function MemoryBrowserScreen() {
  const [query, setQuery] = useState("recent");
  const [submitted, setSubmitted] = useState("recent");

  const memoryQuery = useQuery({
    queryKey: MEMORY_QUERY_KEY(submitted),
    queryFn: () => getGatewaySubagentReports({ data: { query: submitted, topK: 20 } }),
    staleTime: 20_000,
  });

  const entries: MemoryEntry[] = Array.isArray(memoryQuery.data) ? memoryQuery.data : [];

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (query.trim()) setSubmitted(query.trim());
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Working memory</h1>
        <p className="text-sm text-muted-foreground mt-1">
          What your agent remembers from past tasks and delegations.
        </p>
      </div>

      <form className="flex gap-2" onSubmit={handleSearch}>
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search memory, e.g. billing, Priya, meetings…"
          className="flex-1"
        />
        <Button type="submit" variant="secondary">
          Search
        </Button>
      </form>

      {memoryQuery.isLoading && (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      )}

      {memoryQuery.isError && (
        <p className="text-destructive text-sm">
          Could not load memory. Make sure the gateway is running with an active session.
        </p>
      )}

      {!memoryQuery.isLoading && !memoryQuery.isError && entries.length === 0 && (
        <div className="py-12 text-center flex flex-col gap-2">
          <p className="text-muted-foreground text-sm">Nothing found for "{submitted}".</p>
          <p className="text-xs text-muted-foreground">
            Memory grows as your agent completes tasks. Try a different search term.
          </p>
        </div>
      )}

      {entries.length > 0 && (
        <div className="flex flex-col gap-3">
          {entries.map((entry, i) => (
            <MemoryCard key={i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}
