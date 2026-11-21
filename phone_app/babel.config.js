module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
    // The worklets plugin has to be the last one.
    plugins: ["react-native-worklets/plugin"],
  };
};
