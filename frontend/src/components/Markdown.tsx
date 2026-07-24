import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import {
  Typography,
  Link,
  Box,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  Divider,
} from '@mui/material';

// MUI-styled renderers for markdown emitted by the agents (headings, lists,
// GitHub-flavored tables, bold, code, blockquotes). Theme-aware via MUI colors.
const components: Components = {
  h1: ({ children }) => (
    <Typography variant="h5" sx={{ mt: 2, mb: 1, fontWeight: 700 }}>
      {children}
    </Typography>
  ),
  h2: ({ children }) => (
    <Typography variant="h6" sx={{ mt: 2, mb: 1, fontWeight: 700 }}>
      {children}
    </Typography>
  ),
  h3: ({ children }) => (
    <Typography variant="subtitle1" sx={{ mt: 1.5, mb: 0.5, fontWeight: 700 }}>
      {children}
    </Typography>
  ),
  h4: ({ children }) => (
    <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5, fontWeight: 700 }}>
      {children}
    </Typography>
  ),
  p: ({ children }) => (
    <Typography variant="body2" sx={{ my: 1, lineHeight: 1.7 }}>
      {children}
    </Typography>
  ),
  ul: ({ children }) => (
    <Box component="ul" sx={{ my: 1, pl: 3, '& li': { mb: 0.5 } }}>
      {children}
    </Box>
  ),
  ol: ({ children }) => (
    <Box component="ol" sx={{ my: 1, pl: 3, '& li': { mb: 0.5 } }}>
      {children}
    </Box>
  ),
  li: ({ children }) => (
    <Typography component="li" variant="body2" sx={{ lineHeight: 1.6 }}>
      {children}
    </Typography>
  ),
  a: ({ href, children }) => (
    <Link href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </Link>
  ),
  strong: ({ children }) => (
    <Box component="strong" sx={{ fontWeight: 700 }}>
      {children}
    </Box>
  ),
  em: ({ children }) => <Box component="em" sx={{ fontStyle: 'italic' }}>{children}</Box>,
  hr: () => <Divider sx={{ my: 2 }} />,
  blockquote: ({ children }) => (
    <Box
      sx={{
        borderLeft: 3,
        borderColor: 'divider',
        pl: 2,
        my: 1.5,
        color: 'text.secondary',
      }}
    >
      {children}
    </Box>
  ),
  code: ({ className, children }) => {
    const isBlock = (className ?? '').includes('language-');
    if (isBlock) {
      return (
        <Box
          component="pre"
          sx={{
            my: 1.5,
            p: 1.5,
            borderRadius: 1,
            bgcolor: 'action.hover',
            overflowX: 'auto',
            fontFamily: 'monospace',
            fontSize: '0.8rem',
          }}
        >
          <code>{children}</code>
        </Box>
      );
    }
    return (
      <Box
        component="code"
        sx={{
          px: 0.5,
          py: 0.2,
          borderRadius: 0.5,
          bgcolor: 'action.hover',
          fontFamily: 'monospace',
          fontSize: '0.85em',
        }}
      >
        {children}
      </Box>
    );
  },
  table: ({ children }) => (
    <Box sx={{ overflowX: 'auto', my: 2 }}>
      <Table size="small" sx={{ minWidth: 300 }}>
        {children}
      </Table>
    </Box>
  ),
  thead: ({ children }) => <TableHead>{children}</TableHead>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => (
    <TableCell sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{children}</TableCell>
  ),
  td: ({ children }) => <TableCell>{children}</TableCell>,
};

interface MarkdownProps {
  children: string;
}

export const Markdown: React.FC<MarkdownProps> = ({ children }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
    {children}
  </ReactMarkdown>
);

export default Markdown;
