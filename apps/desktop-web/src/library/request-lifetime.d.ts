export function createRequestLifetime(): {
  activate(): void;
  end(): void;
  bind<T extends (...args: any[]) => Promise<any>>(request: T): T;
};
