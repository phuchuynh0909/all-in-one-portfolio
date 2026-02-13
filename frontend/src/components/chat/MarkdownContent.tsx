import { Box, Link, Typography } from '@mui/material';

interface MarkdownContentProps {
  content: string;
  sx?: object;
}

/**
 * Lightweight markdown renderer for chat messages.
 * Handles: **bold**, [links](url), bullet lists, numbered lists, headings.
 */
interface ListNode {
  content: string;
  indent: number;
  children: ListNode[];
}

const listItemStyles = {
  margin: '0.5em 0',
  paddingLeft: '1.5em',
  listStylePosition: 'outside' as const,
  '& li': {
    mb: 0.5,
    display: 'list-item' as const,
    paddingLeft: '0.5em',
    marginLeft: '0.5em',
  },
  '& ul': { listStyleType: 'circle' as const },
  '& ul ul': { listStyleType: 'disc' as const },
};

function buildListTree(items: { content: string; indent: number }[]): ListNode[] {
  const root: ListNode = { content: '', indent: -1, children: [] };
  const stack: ListNode[] = [root];

  for (const item of items) {
    const node: ListNode = { content: item.content, indent: item.indent, children: [] };
    while (stack.length > 1 && item.indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  return root.children;
}

export const MarkdownContent = ({ content, sx = {} }: MarkdownContentProps) => {
  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];
  let listItems: { content: string; indent: number }[] = [];
  let listOrdered = false;

  const renderListTree = (nodes: ListNode[], ordered: boolean, keyPrefix: string) => {
    const ListTag = ordered ? 'ol' : 'ul';
    return (
      <Box
        component={ListTag}
        sx={{
          ...listItemStyles,
          ...(ordered ? {} : { listStyleType: 'disc' }),
        }}
      >
        {nodes.map((node, i) => (
          <Box component="li" key={`${keyPrefix}-${i}`}>
            <InlineMarkdown text={node.content} />
            {node.children.length > 0 && renderListTree(node.children, false, `${keyPrefix}-${i}`)}
          </Box>
        ))}
      </Box>
    );
  };

  const flushList = () => {
    if (listItems.length === 0) return;
    const tree = buildListTree(listItems);
    elements.push(
      <Box key={elements.length}>
        {renderListTree(tree, listOrdered, `list-${elements.length}`)}
      </Box>,
    );
    listItems = [];
  };

  const InlineMarkdown = ({ text }: { text: string }) => {
    const parts: React.ReactNode[] = [];
    let remaining = text;
    let key = 0;

    while (remaining.length > 0) {
      const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
      const linkMatch = remaining.match(/\[([^\]]+)\]\(([^)]+)\)/);
      const codeMatch = remaining.match(/`([^`]+)`/);

      let earliest = Infinity;
      let match: RegExpMatchArray | null = null;
      let type: 'bold' | 'link' | 'code' | null = null;

      if (boldMatch && boldMatch.index !== undefined && boldMatch.index < earliest) {
        earliest = boldMatch.index;
        match = boldMatch;
        type = 'bold';
      }
      if (linkMatch && linkMatch.index !== undefined && linkMatch.index < earliest) {
        earliest = linkMatch.index;
        match = linkMatch;
        type = 'link';
      }
      if (codeMatch && codeMatch.index !== undefined && codeMatch.index < earliest) {
        earliest = codeMatch.index;
        match = codeMatch;
        type = 'code';
      }

      if (match && type) {
        if (earliest > 0) {
          parts.push(
            <span key={key++}>{remaining.slice(0, earliest)}</span>,
          );
        }
        if (type === 'bold') {
          parts.push(
            <Box component="strong" key={key++} sx={{ fontWeight: 600 }}>
              <InlineMarkdown text={match[1]} />
            </Box>,
          );
        } else if (type === 'link') {
          parts.push(
            <Link
              key={key++}
              href={match[2]}
              target="_blank"
              rel="noopener noreferrer"
              sx={{ color: 'primary.main', textDecoration: 'underline' }}
            >
              {match[1]}
            </Link>,
          );
        } else if (type === 'code') {
          parts.push(
            <Box
              key={key++}
              component="code"
              sx={{
                px: 0.5,
                py: 0.25,
                borderRadius: 0.5,
                bgcolor: 'action.hover',
                fontFamily: 'monospace',
                fontSize: '0.9em',
              }}
            >
              {match[1]}
            </Box>,
          );
        }
        remaining = remaining.slice(earliest + match[0].length);
      } else {
        parts.push(<span key={key++}>{remaining}</span>);
        break;
      }
    }

    return <>{parts}</>;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === '') {
      flushList();
      elements.push(<Box key={elements.length} sx={{ height: '0.5em' }} />);
      continue;
    }

    const leadingSpaces = line.match(/^(\s*)/)?.[1]?.replace(/\t/g, '  ').length ?? 0;
    const indent = Math.floor(leadingSpaces / 2);
    const bulletMatch = trimmed.match(/^[-*]\s+(.*)/);
    const orderedMatch = trimmed.match(/^\d+\.\s+(.*)/);
    const h3Match = trimmed.match(/^###\s+(.*)/);
    const h2Match = trimmed.match(/^##\s+(.*)/);
    const h1Match = trimmed.match(/^#\s+(.*)/);
    const hrMatch = trimmed.match(/^-{2,}$/);

    if (hrMatch) {
      flushList();
      elements.push(
        <Box key={elements.length} sx={{ borderTop: 1, borderColor: 'divider', my: 1.5 }} />,
      );
    } else if (h1Match) {
      flushList();
      elements.push(
        <Typography key={elements.length} variant="h6" sx={{ fontWeight: 600, mt: 1.5, mb: 0.5 }}>
          <InlineMarkdown text={h1Match[1]} />
        </Typography>,
      );
    } else if (h2Match) {
      flushList();
      elements.push(
        <Typography key={elements.length} variant="subtitle1" sx={{ fontWeight: 600, mt: 1.25, mb: 0.5 }}>
          <InlineMarkdown text={h2Match[1]} />
        </Typography>,
      );
    } else if (h3Match) {
      flushList();
      elements.push(
        <Typography key={elements.length} variant="subtitle2" sx={{ fontWeight: 600, mt: 1, mb: 0.25 }}>
          <InlineMarkdown text={h3Match[1]} />
        </Typography>,
      );
    } else if (bulletMatch) {
      if (listItems.length > 0 && !listOrdered) {
        listItems.push({ content: bulletMatch[1], indent });
      } else {
        flushList();
        listOrdered = false;
        listItems = [{ content: bulletMatch[1], indent }];
      }
    } else if (orderedMatch) {
      if (listItems.length > 0 && listOrdered) {
        listItems.push({ content: orderedMatch[1], indent });
      } else {
        flushList();
        listOrdered = true;
        listItems = [{ content: orderedMatch[1], indent }];
      }
    } else {
      flushList();
      elements.push(
        <Typography key={elements.length} component="div" sx={{ mb: 0.5, '&:last-of-type': { mb: 0 } }}>
          <InlineMarkdown text={trimmed} />
        </Typography>,
      );
    }
  }
  flushList();

  return (
    <Box
      sx={{
        '& p': { margin: 0 },
        '& strong': { fontWeight: 600 },
        ...sx,
      }}
    >
      {elements}
    </Box>
  );
};
