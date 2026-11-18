import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { CopyButton } from "../ui/CopyButton";
import { EmptyState } from "../ui/EmptyState";

type Props = {
  children: ReactNode;
  /** Changing this value clears the error (pass the route, so navigating away recovers). */
  resetKey?: string;
};

type State = { error: Error | null; componentStack: string; resetKey: string | undefined };

/** Catches render errors in a region and offers the two useful things: details and a reload. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null, componentStack: "", resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey === state.resetKey) return null;
    return { error: null, componentStack: "", resetKey: props.resetKey };
  }

  override componentDidCatch(_error: Error, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? "" });
  }

  override render(): ReactNode {
    const { error, componentStack } = this.state;
    if (!error) return this.props.children;
    const details = [
      `${error.name}: ${error.message}`,
      error.stack ?? "",
      componentStack ? `Component stack:${componentStack}` : "",
      `Location: ${window.location.hash || "#/"}`,
      `User agent: ${navigator.userAgent}`,
    ]
      .filter(Boolean)
      .join("\n\n");
    return (
      <div role="alert" className="h-full overflow-auto">
        <EmptyState
          icon="error"
          tone="error"
          title="This view failed to render"
          actions={
            <>
              <Button variant="primary" icon="reload" onClick={() => window.location.reload()}>
                Reload
              </Button>
              <CopyButton text={details} label="Copy details" />
            </>
          }
        >
          <p className="m-0">
            The viewer hit a bug while drawing this screen. Your data is untouched. Copy the details
            into a bug report, or reload to try again.
          </p>
          <pre className="m-0 max-h-40 overflow-auto rounded-control border border-line bg-surface p-2 text-data text-fg">
            {error.name}: {error.message}
          </pre>
        </EmptyState>
      </div>
    );
  }
}
