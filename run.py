import os
import sys
import subprocess
import numpy as np
from time import time, sleep
import argparse
import json
import copy
#from tqdm import tqdm
import progressbar
import torch
import gym
import gym_auv
import gym_auv.reporting
import multiprocessing

from stable_baselines3.common.utils import set_random_seed
from stable_baselines3.common.vec_env import VecVideoRecorder, DummyVecEnv, SubprocVecEnv, VecFrameStack
from stable_baselines3.common.noise import NormalActionNoise, OrnsteinUhlenbeckActionNoise
from stable_baselines3 import PPO, DDPG, TD3, A2C, SAC
from sklearn.model_selection import ParameterGrid
from shapely import speedups

from stable_baselines3.common.callbacks import EveryNTimesteps, EventCallback, BaseCallback, EvalCallback
import queue
from collections import deque
import matplotlib.pyplot as plt


### HANNAH
#from gym_auv.utils.radarCNN import LidarCNN_pretrained, PerceptionNavigationExtractor

### THOMAS
from gym_auv.utils.radarCNN import RadarCNN, PerceptionNavigationExtractor



speedups.enable()
DIR_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _preprocess_custom_envconfig(rawconfig):
    custom_envconfig = dict(zip(args.envconfig[::2], args.envconfig[1::2]))
    for key in custom_envconfig:
        try:
            custom_envconfig[key] = float(custom_envconfig[key])
            if (custom_envconfig[key] == int(custom_envconfig[key])):
                custom_envconfig[key] = int(custom_envconfig[key])
        except ValueError:
            pass
    return custom_envconfig


def create_env(env_id, envconfig, test_mode=False, render_mode='2d', pilot=None, verbose=False):
    if pilot:
        env = gym.make(env_id, env_config=envconfig, test_mode=test_mode, render_mode=render_mode, pilot=pilot, verbose=verbose)
    else:
        env = gym.make(env_id, env_config=envconfig, test_mode=test_mode, render_mode=render_mode, verbose=verbose)
    return env


def make_mp_env(env_id, rank, envconfig, seed=0, pilot=None):
    """
    Utility function for multiprocessed env.
    :param env_id: (str) the environment ID
    :param num_env: (int) the number of environments you wish to have in subprocesses
    :param seed: (int) the inital seed for RNG
    :param rank: (int) index of the subprocess
    """
    def _init():
        env = create_env(env_id, envconfig, pilot=pilot)
        env.seed(seed + rank)

        if envconfig['safety_filter']:
            #activate safety filter with rank
            env.vessel.activate_safety_filter(env, rank)

        return env
    set_random_seed(seed)
    return _init


def play_scenario(env, recorded_env, args, agent=None):
    # if args.video:
    #     print('Recording enabled')
    #     recorded_env = VecVideoRecorder(env, args.video_dir, record_video_trigger=lambda x: x == 0, 
    #         video_length=args.recording_length, name_prefix=args.video_name
    #     )

    from pyglet.window import key

    key_input = np.array([-1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    autopilot = False

    # gail_expert_generation = False
    # gail_actions = []
    # gail_observations = []
    # gail_rewards = []
    # gail_num_episodes = 2
    # gail_episode_returns = np.zeros((gail_num_episodes,))
    # gail_episode_starts = []
    # gail_reward_sum = 0
    # gail_ep_idx = 0
    

    print('Playing scenario: ', env)

    def key_press(k, mod):
        nonlocal autopilot
        if k == key.DOWN:  key_input[0] = 0
        if k == key.UP:    key_input[0] = 1
        if k == key.LEFT:  key_input[1] = 0.5
        if k == key.RIGHT: key_input[1] = -0.5
        if k == key.NUM_2: key_input[2] = -1
        if k == key.NUM_1: key_input[2] = 1
        if k == key.J: key_input[3] = -1
        if k == key.U: key_input[3] = 1
        if k == key.I: key_input[4] = -1
        if k == key.K: key_input[4] = 1
        if k == key.O: key_input[5] = -1
        if k == key.P: key_input[5] = 1
        if k == key.NUM_4: key_input[6] = -1
        if k == key.NUM_3: key_input[6] = 1
        if k == key.A: 
            autopilot = not autopilot
            print('Autopilot {}'.format(autopilot))
        # if k == key.E: 
        #     gail_expert_generation = not gail_expert_generation
        #     print('gail_expert_generation {}'.format(gail_expert_generation))

    def key_release(k, mod):
        nonlocal restart, quit
        if k == key.R:
            restart = True
            print('Restart')
        if k == key.P:
            from gym_auv.rendering.render2d import save_screenshot
            save_screenshot(env, 'screenshot.png')
            print('Saved screenshot to screenshot.png')
        if k == key.Q:
            quit = True
            print('quit')
        if k == key.UP:    key_input[0] = -1
        if k == key.DOWN:  key_input[0] = -1
        if k == key.LEFT and key_input[1] != 0: key_input[1] = 0
        if k == key.RIGHT and key_input[1] != 0: key_input[1] = 0
        if k == key.NUM_2 and key_input[2] != 0: key_input[2] = 0
        if k == key.NUM_1 and key_input[2] != 0: key_input[2] = 0
        if k == key.U and key_input[3] != 0: key_input[3] = 0
        if k == key.J and key_input[3] != 0: key_input[3] = 0
        if k == key.I and key_input[4] != 0: key_input[4] = 0
        if k == key.K and key_input[4] != 0: key_input[4] = 0
        if k == key.O and key_input[5] != 0: key_input[5] = 0
        if k == key.P and key_input[5] != 0: key_input[5] = 0
        if k == key.NUM_4 and key_input[6] != 0: key_input[6] = 0
        if k == key.NUM_3 and key_input[6] != 0: key_input[6] = 0


    viewer = env.env._viewer2d if args.render in {'both', '2d'} else env.viewer3d
    viewer.window.on_key_press = key_press
    viewer.window.on_key_release = key_release

    env.reset()

    if env.config['safety_filter']:
        #activate safety filter
        env.vessel.activate_safety_filter(env, 0)
    try:
        while True:
            t = time()
            restart = False
            t_steps = 0
            quit = False
            if (args.env == 'PathGeneration-v0'):
                a = np.array([5.0, 5.0, 1.0, 1.0])
            elif (args.env == 'PathColavControl-v0'):
                a = np.array([0.0])
            else:
                a = np.array([0.0, 0.0])

            obs = None
            while True:
                t, dt = time(), time()-t
                if args.env == 'PathGeneration-v0':
                    a[0] += key_input[1]
                    a[1] = max(0, key_input[0], a[1] + 0.1*key_input[0])
                    a[2] += 0.1*key_input[2]
                    print('Applied action: ', a)
                    sleep(1)
                elif (args.env == 'PathColavControl-v0'):
                    a[0] = 0.1*key_input[1]
                else:
                    a[0] = key_input[0]
                    a[1] = key_input[1]
                    try:
                        env.rewarder.params["lambda"] = np.clip(np.power(10, np.log10(env.rewarder.params["lambda"]) + key_input[2]*0.05), 0, 1)
                        env.rewarder.params["eta"] = np.clip(env.rewarder.params["eta"] + key_input[6]*0.02, 0, 4)
                    except KeyError:
                        pass
                    if args.render in {'3d', 'both'}:
                        env.viewer3d.camera_height += 0.15*key_input[3]
                        env.viewer3d.camera_height = max(0, env.viewer3d.camera_height)
                        env.viewer3d.camera_distance += 0.3*key_input[4]
                        env.viewer3d.camera_distance = max(1, env.viewer3d.camera_distance)
                        env.viewer3d.camera_angle += 0.3*key_input[5]

                    elif args.render == '2d':
                        env.env._viewer2d.camera_zoom += 0.1*key_input[4]
                        env.env._viewer2d.camera_zoom = max(0, env.env._viewer2d.camera_zoom)

                if autopilot and agent is not None:
                    if obs is None:
                        a = np.array([0.0, 0.0])
                    else:
                        a, _ = agent.predict(obs, deterministic=True)

                obs, r, done, info = env.step(a)   

                
                # gail_observations.append(obs)
                # gail_actions.append(a)
                # gail_rewards.append(r)
                # gail_episode_starts.append(done)
                # gail_reward_sum += r

                # if gail_ep_idx >= gail_num_episodes and gail_expert_generation:
                #     break

                if args.verbose > 0:
                    print(', '.join('{:.1f}'.format(x) for x in obs) + '(size {})'.format(len(obs)))
                recorded_env.render()
                t_steps += 1

                if args.save_snapshots and not done:
                    if t_steps % 50 == 0:
                        env.save_latest_episode(save_history=False)
                        for size in (100, 200):#, 300, 400, 500):
                            gym_auv.reporting.plot_trajectory(
                                figure_folder, env, fig_dir='logs/play_results/', fig_prefix=('_t_step_' + str(t_steps) + '_' + str(size)), local=True, size=size
                            )

                if quit: raise KeyboardInterrupt
                if done or restart: 
                    # gail_episode_returns[gail_ep_idx] = gail_reward_sum
                    # gail_reward_sum = 0
                    # gail_ep_idx += 1
                    break
            
            env.seed(np.random.randint(1000))
            env.save_latest_episode()
            #gym_auv.reporting.report(env, report_dir='logs/play_results/')
            #gym_auv.reporting.plot_trajectory(figure_folder, env, fig_dir='logs/play_results/')
            env.reset(save_history=False)

            
        
            # if gail_ep_idx >= gail_num_episodes and gail_expert_generation:
            #     gail_observations = np.concatenate(gail_observations).reshape((-1,) + env.observation_space.shape)
            #     gail_actions = np.concatenate(gail_actions).reshape((-1,) + env.action_space.shape)
            #     gail_rewards = np.array(gail_rewards)
            #     gail_episode_starts = np.array(gail_episode_starts[:-1])
            #     gail_numpy_dict = {
            #     'actions': gail_actions,
            #     'obs': gail_observations,
            #     'rewards': gail_rewards,
            #     'episode_returns': gail_episode_returns,
            #     'episode_starts': gail_episode_starts
            #     }
            #     np.savez('gail_expert', **gail_numpy_dict)
            
            

    except KeyboardInterrupt:
        pass

def main(args):
    envconfig_string = args.envconfig
    custom_envconfig = _preprocess_custom_envconfig(args.envconfig) if args.envconfig is not None else {}
    env_id = 'gym_auv:' + args.env
    env_name = env_id.split(':')[-1] if ':' in env_id else env_id
    envconfig = gym_auv.SCENARIOS[env_name]['config'] if env_name in gym_auv.SCENARIOS else {}  
    envconfig.update(custom_envconfig)

    #NUM_CPU = multiprocessing.cpu_count()
    NUM_CPU = 8 #8
    #torch.set_num_threads(multiprocessing.cpu_count()//4)
    #print("Pytorch using {} threads".format(torch.get_num_threads()))

    EXPERIMENT_ID = str(int(time())) + args.algo.lower()
    model = {
        'ppo': PPO,
        'ddpg': DDPG,
        'td3': TD3,
        'a2c': A2C,
        'sac': SAC
    }[args.algo.lower()]

    if args.mode == 'play':
        agent = model.load(args.agent) if args.agent is not None else None
        envconfig_play = envconfig.copy()
        envconfig_play['show_indicators'] = True
        #envconfig_play['autocamera3d'] = False
        env = create_env(env_id, envconfig_play, test_mode=True, render_mode=args.render, pilot=args.pilot, verbose=True)
        print('Created environment instance')

        if args.scenario:
            env.load(args.scenario)
        vec_env = DummyVecEnv([lambda: env])
        recorded_env = VecVideoRecorder(vec_env, args.video_dir, record_video_trigger=lambda x: x==0, 
            video_length=args.recording_length, name_prefix=(args.env if args.video_name == 'auto' else args.video_name)
        )
        print(args.video_dir, args.video_name)

        play_scenario(env, recorded_env, args, agent=agent)
        recorded_env.env.close()

    elif (args.mode == 'enjoy'):
        agent = model.load(args.agent)

        figure_folder = os.path.join(DIR_PATH, 'logs', 'enjoys', args.env, EXPERIMENT_ID)
        os.makedirs(figure_folder, exist_ok=True)
        scenario_folder = os.path.join(figure_folder, 'scenarios')
        os.makedirs(scenario_folder, exist_ok=True)

        video_folder = os.path.join(DIR_PATH, 'logs', 'videos', args.env, EXPERIMENT_ID)
        os.makedirs(video_folder, exist_ok=True)
        
        env = create_env(env_id, envconfig, test_mode=True, render_mode=args.render, pilot=args.pilot)
        if args.scenario:
            env.load(args.scenario)
        _vec_env = DummyVecEnv([lambda: env])
        vec_env = VecFrameStack(_vec_env, n_stack=1, channels_order='first')
        recorded_env = VecVideoRecorder(vec_env, video_folder, record_video_trigger=lambda x: x==0, 
            video_length=args.recording_length, name_prefix=(args.env if args.video_name == 'auto' else args.video_name)
        )
        obs = recorded_env.reset()

        if envconfig['safety_filter']:
            #activate safety filter
            env.vessel.activate_safety_filter(env, 0)

        state = None
        t_steps = 0
        ep_number = 1
        done = [False for _ in range(vec_env.num_envs)]
        for _ in range(args.recording_length):
            if args.recurrent:
                action, _states = agent.predict(observation=obs, state=state, mask=done, deterministic=not args.stochastic)
                state = _states
            else:
                action, _states = agent.predict(obs, deterministic=not args.stochastic)
            obs, reward, done, info = recorded_env.step(action)
            recorded_env.render()
            t_steps += 1
            
            if t_steps % 800 == 0 or done:
                if not done:
                    env.save_latest_episode(save_history=False)
                #gym_auv.reporting.plot_trajectory(figure_folder, env, fig_dir=scenario_folder, fig_prefix=(args.env + '_ep{}_step{}'.format(ep_number, t_steps)))
                #gym_auv.reporting.plot_trajectory(figure_folder, env, fig_dir=scenario_folder, fig_prefix=(args.env + '_ep{}_step{}_local'.format(ep_number, t_steps)), local=True)
            if done:
                ep_number += 1
        recorded_env.close()

    elif (args.mode == 'train'):
        figure_folder = os.path.join(DIR_PATH, 'logs', 'figures', args.env, EXPERIMENT_ID)
        os.makedirs(figure_folder, exist_ok=True)
        scenario_folder = os.path.join(figure_folder, 'scenarios')
        os.makedirs(scenario_folder, exist_ok=True)
        video_folder = os.path.join(DIR_PATH, 'logs', 'videos', args.env, EXPERIMENT_ID)
        recording_length = 8000
        os.makedirs(video_folder, exist_ok=True)
        agent_folder = os.path.join(DIR_PATH, 'logs', 'agents', args.env, EXPERIMENT_ID)
        os.makedirs(agent_folder, exist_ok=True)
        tensorboard_log = os.path.join(DIR_PATH, 'logs', 'tensorboard', args.env, EXPERIMENT_ID)
        tensorboard_port = 6006

        if args.nomp or model == DDPG or model == TD3 or model == SAC:
            num_cpu = 1
            vec_env = DummyVecEnv([lambda: create_env(env_id, envconfig, pilot=args.pilot)])
        else:
            num_cpu = NUM_CPU
            vec_env = SubprocVecEnv([make_mp_env(env_id, i, envconfig, pilot=args.pilot) for i in range(num_cpu)])
            #vec_env = VecFrameStack(_vec_env, n_stack=1, channels_order='first')

        if (args.agent is not None):
            agent = model.load(args.agent)
            agent.set_env(vec_env)


        else:
            if (model == PPO):
                if args.recurrent:
                    hyperparams = {
                        # 'n_steps': 1024,
                        # 'nminibatches': 32,
                        # 'lam': 0.95,
                        # 'gamma': 0.99,
                        # 'noptepochs': 10,
                        # 'ent_coef': 0.0,
                        # 'learning_rate': 0.0003,
                        # 'cliprange': 0.2,
                        'n_steps': 1024,
                        'batch_size': 1,
                        'lam': 0.98,
                        'gamma': 0.999,
                        'noptepochs': 4,
                        'ent_coef': 0.01,
                        'learning_rate': 2e-3,
                    }
                    class CustomLSTMPolicy(MlpLstmPolicy):
                        def __init__(self, sess, ob_space, ac_space, n_env, n_steps, n_batch, n_lstm=256, reuse=False, **_kwargs):
                            super().__init__(sess, ob_space, ac_space, n_env, n_steps, n_batch, n_lstm, reuse,
                            net_arch=[256, 256, 'lstm', dict(vf=[64], pi=[64])],
                            **_kwargs)

                    agent = PPO(CustomLSTMPolicy,
                        vec_env, verbose=True, tensorboard_log=tensorboard_log, 
                        **hyperparams
                    )
                else:
                    hyperparams = {
                        # 'n_steps': 1024,
                        # 'nminibatches': 32,
                        # 'lam': 0.95,
                        # 'gamma': 0.99,
                        # 'noptepochs': 10,
                        # 'ent_coef': 0.0,
                        # 'learning_rate': 0.0003,
                        # 'cliprange': 0.2,
                        'n_steps': 1024,       # Default 128
                        'batch_size': 32,      # Default 4
                        'gae_lambda': 0.98,    # Default 0.95
                        'gamma': 0.999,        # Default 0.99
                        'n_epochs': 4,         # Default 4
                        'ent_coef': 0.01,      # Default 0.01
                        'learning_rate': 2e-4, # Default 2.5e-4
                    }
                    #policy_kwargs = dict(act_fun=tf.nn.tanh, net_arch=[64, 64, 64])
                    #policy_kwargs = dict(net_arch=[64, 64, 64])
                    #layers = [256, 128, 64]
                    #layers = [64, 64]
                    #policy_kwargs = dict(net_arch = [dict(vf=layers, pi=layers)])
                    policy_kwargs = dict(
                        features_extractor_class = PerceptionNavigationExtractor,
                        features_extractor_kwargs = dict(features_dim=12),
                        #net_arch = [128, 64, dict(pi=[32]), dict(vf=[32])]
                        #net_arch=[dict(pi=[64, 64], vf=[64, 64])]
                        net_arch=[dict(pi=[128, 64, 32], vf=[128, 64, 32])]
                    )
                    agent = PPO("MlpPolicy",
                        vec_env, verbose=True, tensorboard_log=tensorboard_log, 
                        **hyperparams, policy_kwargs=policy_kwargs
                    )
                    print("Agent network construction:")
                    print("CNN Feature Extractor:", agent.policy.features_extractor.extractors["perception"])
                    print("Navigation Passthrough:", agent.policy.features_extractor.extractors["navigation"])
                    #dataset = ExpertDataset(expert_path='gail_expert.npz', traj_limitation=1, batch_size=128)
                    #print('Pretraining {} agent on "{}"'.format(args.algo.upper(), env_id))
                    #agent.pretrain(dataset, n_epochs=1000)
                    #print('Done pretraining {} agent on "{}"'.format(args.algo.upper(), env_id))
            elif (model == DDPG):
                # rl-baselines-zoo inspired:
                # hyperparams = {
                #     'memory_limit': 50000,
                #     'normalize_observations': True,
                #     'normalize_returns': False,
                #     'gamma': 0.98,
                #     'actor_lr': 0.00156,
                #     'critic_lr': 0.00156,
                #     'batch_size': 256,
                #     'param_noise': AdaptiveParamNoiseSpec(initial_stddev=0.1, desired_action_stddev=0.1)
                # }
                hyperparams = {
                    'memory_limit': 1000000,            # Default None (DEPRECATED: use buffer_size instead: 50000)
                    'normalize_observations': True,     # Default False
                    'normalize_returns': False,         # Default False
                    'gamma': 0.98,                      # Default 0.99
                    'actor_lr': 0.00156,                # Default 0.0001
                    'critic_lr': 0.00156,               # Default 0.001
                    'batch_size': 256,                  # Default 128
                    # OpenAI Baselines aim for action_space_stddev = 0.2 for continuous dense cases.
                    #'param_noise': AdaptiveParamNoiseSpec(initial_stddev=0.287, desired_action_stddev=0.287)
                    # DDPG Paper recommends to add this:
                    # OrnsteinUhlenbeckActionNoise(mean=np.zeros(n_actions), sigma=float(0.5) * np.ones(n_actions))????
                    # As action noise to encourage exploration.
                    'action_noise': OrnsteinUhlenbeckActionNoise(mean=np.zeros(2), sigma=float(0.5) * np.ones(2))
                }
                agent = DDPG("MlpPolicy",
                    vec_env, verbose=True, tensorboard_log=tensorboard_log, **hyperparams
                )
            elif (model == TD3):
                # rl-baselines-zoo inspired:
                # hyperparams = {
                #     'batch_size': 256,
                #     'buffer_size': 50000,
                #     'learning_starts': 1000
                # }
                hyperparams = {
                    'buffer_size': 1000000,     # Default 50000
                    'train_freq': 1000,         # Default 100
                    'gradient_steps': 1000,     # Default 100
                    'learning_starts': 10000    # Default 100
                }
                action_noise = NormalActionNoise(mean=np.zeros(2), sigma=0.1*np.ones(2))
                agent = TD3("MlpPolicy",
                    vec_env, verbose=True, tensorboard_log=tensorboard_log, action_noise=action_noise, **hyperparams
                )
            elif model == A2C:
                # rl-baselines-zoo inspired:
                # hyperparams = {
                #     'n_steps': 5,
                #     'gamma': 0.995,
                #     'ent_coef': 0.00001,
                #     'learning_rate': 0.00083,
                #     'lr_schedule': 'linear'
                # }
                # layers = [256, 128, 64]
                hyperparams = {
                    'n_steps': 16,              # Default 5
                    'gamma': 0.99,              # Default 0.99
                    'ent_coef': 0.001,          # Default 0.01
                    'learning_rate': 2e-4,      # Default 7e-4
                    'lr_schedule': 'linear'     # Default 'constant'  (learning rate updates)
                }
                layers = [64, 64]
                policy_kwargs = dict(net_arch = [dict(vf=layers, pi=layers)])
                agent = A2C("MlpPolicy",
                    vec_env, verbose=True, tensorboard_log=tensorboard_log, 
                    **hyperparams, policy_kwargs=policy_kwargs
                )
            elif model == SAC:
                # rl-baselines-zoo inspired:
                # hyperparams = {
                #     'batch_size': 256,
                #     'learning_starts': 1000
                # }
                # agent = SAC("MlpPolicy", vec_env, verbose=True, tensorboard_log=tensorboard_log, **hyperparams)
                '''                                                             LunarLanderContinuous-v2:
                    learning_rate: Union[float, Schedule] = 3e-4,               : lin_7.3e-4
                    buffer_size: int = 1000000,  # 1e6                          : same
                    learning_starts: int = 100,                                 : 10000
                    batch_size: int = 256,                                      : same
                    tau: float = 0.005,                                         : 0.01
                    gamma: float = 0.99,                                        : same
                    train_freq: Union[int, Tuple[int, str]] = 1,                : 1
                    gradient_steps: int = 1,                                    : 1
                    action_noise: Optional[ActionNoise] = None,                 : ---
                    replay_buffer_class: Optional[ReplayBuffer] = None,         : ---
                    replay_buffer_kwargs: Optional[Dict[str, Any]] = None,      : ---
                    optimize_memory_usage: bool = False,                        : ---
                    ent_coef: Union[str, float] = "auto",                       : 'auto'
                    target_update_interval: int = 1,                            : ---
                    target_entropy: Union[str, float] = "auto",                 : ---
                    use_sde: bool = False,                                      : ---
                    sde_sample_freq: int = -1,                                  : ---
                    use_sde_at_warmup: bool = False,                            : ---
                    tensorboard_log: Optional[str] = None,                      : ---
                    create_eval_env: bool = False,                              : ---
                    policy_kwargs: Optional[Dict[str, Any]] = None,             : "dict(net_arch=[400, 300])"
                    verbose: int = 0,                                           : ---
                    seed: Optional[int] = None,                                 : ---
                    device: Union[th.device, str] = "auto",                     : ---
                    _init_setup_model: bool = True,                             : ---
                '''
                policy_kwargs = dict(
                    features_extractor_class=PerceptionNavigationExtractor,
                    features_extractor_kwargs=dict(features_dim=32),
                    # net_arch = [128, 64, dict(pi=[32]), dict(vf=[32])]
                    # net_arch=[dict(pi=[64, 64], vf=[64, 64])]
                    #net_arch=[dict(pi=[128, 64, 32], qf=[128, 64, 32])]
                    net_arch=[128, 64, 32]
                )
                agent = SAC("MlpPolicy", vec_env, verbose=True, tensorboard_log=tensorboard_log,
                            policy_kwargs=policy_kwargs
                            )

        print('Training {} agent on "{}"'.format(args.algo.upper(), env_id))

        '''
        n_updates = 0
        n_episodes = 0
        def callback(_locals, _globals):
            nonlocal n_updates
            nonlocal n_episodes
            sys.stdout.write('Training update: {}\r'.format(n_updates))
            sys.stdout.flush()
            _self = _locals['self']
            vec_env = _self.get_env()
            class Struct(object): pass
            report_env = Struct()
            report_env.history = []
            report_env.config = envconfig
            report_env.nsensors = report_env.config["n_sensors_per_sector"]*report_env.config["n_sectors"]
            report_env.sensor_angle = 2*np.pi/(report_env.nsensors + 1)
            report_env.last_episode = vec_env.get_attr('last_episode')[0]
            report_env.config = vec_env.get_attr('config')[0]
            report_env.obstacles = vec_env.get_attr('obstacles')[0]
            env_histories = vec_env.get_attr('history')
            for episode in range(max(map(len, env_histories))):
                for env_idx in range(len(env_histories)):
                    if (episode < len(env_histories[env_idx])):
                        report_env.history.append(env_histories[env_idx][episode])
            report_env.episode = len(report_env.history) + 1
            total_t_steps = _self.get_env().get_attr('total_t_steps')[0]*num_cpu
            agent_filepath = os.path.join(agent_folder, str(total_t_steps) + '.pkl')
            if model == PPO:
                recording_criteria = n_updates % 10 == 0
                report_criteria = True
                _self.save(agent_filepath)
            elif model == A2C:
                save_criteria = n_updates % 100 == 0
                recording_criteria = n_updates % 1000 == 0
                report_criteria = True
                if save_criteria:
                    _self.save(agent_filepath)
            elif model == DDPG or model == TD3 or model == SAC:
                save_criteria = n_updates % 10000 == 0
                recording_criteria = n_updates % 50000 == 0
                report_criteria = report_env.episode > n_episodes
                if save_criteria:
                    _self.save(agent_filepath)
            if report_env.last_episode is not None and len(report_env.history) > 0 and report_criteria:
                try:
                    #gym_auv.reporting.plot_trajectory(report_env, fig_dir=scenario_folder, fig_prefix=args.env + '_ep_{}'.format(report_env.episode))
                    gym_auv.reporting.report(report_env, report_dir=figure_folder)
                    #vec_env.env_method('save', os.path.join(scenario_folder, '_ep_{}'.format(report_env.episode)))
                except OSError as e:
                    print("Ignoring reporting OSError:")
                    print(repr(e))
            if recording_criteria:
                if args.pilot:
                    cmd = 'python run.py enjoy {} --agent "{}" --video-dir "{}" --video-name "{}" --recording-length {} --algo {} --pilot {} --envconfig {}{}'.format(
                        args.env, agent_filepath, video_folder, args.env + '-' + str(total_t_steps), recording_length, args.algo, args.pilot, envconfig_string, 
                        ' --recurrent' if args.recurrent else ''
                    )
                else:
                    cmd = 'python run.py enjoy {} --agent "{}" --video-dir "{}" --video-name "{}" --recording-length {} --algo {} --envconfig {}{}'.format(
                        args.env, agent_filepath, video_folder, args.env + '-' + str(total_t_steps), recording_length, args.algo, envconfig_string, 
                        ' --recurrent' if args.recurrent else ''
                    )
                subprocess.Popen(cmd)
        
            n_episodes = report_env.episode
            n_updates += 1
        '''

        ### CALLBACKS ###
        # Things we want to do: calculate statistics, say 1000 times during training.
        total_timesteps = 1000000 #10000000
        save_stats_freq = total_timesteps // 100  # Save stats 1000 times during training (EveryNTimesteps)
        save_agent_freq = total_timesteps // 10   # Save the agent 100 times throughout training
        record_agent_freq = total_timesteps // 1  # Evaluate and record 10 times during training (EvalCallback)
        # StopTrainingOnRewardThreshold could be used when setting total_timesteps = "inf" and stop the training when the agent is perfect. To see how long it actually takes.
        # CallbackList : [list, of, sequential, callbacks]

        class MaxSizeList(object):
            def __init__(self, max_length):
                self.max_length = max_length
                self.ls = deque(maxlen=max_length)

            def append(self, st):
                if len(self.ls) == self.max_length:
                    self.ls.popleft()
                self.ls.append(st)

            def get_list(self):
                return list(self.ls)

            def __len__(self):
                return len(list(self.ls))

        class CollectStatisticsCallback(BaseCallback):
            def __init__(self, env : SubprocVecEnv, total_timesteps: int, save_stats_freq: int, record_agent_freq: int, log_dir: str, verbose=1):
                super(CollectStatisticsCallback, self).__init__(verbose)
                self.save_stats_freq = save_stats_freq
                self.record_agent_freq = record_agent_freq
                self.save_agent_freq = total_timesteps // 100
                self.log_dir = log_dir
                self.save_path = os.path.join(log_dir, 'agents')
                self.n_episodes = 0
                #self.progress_bar = tqdm(total=total_timesteps)
                self.bar = progressbar.ProgressBar(maxval=total_timesteps,
                                                   widgets=[progressbar.Bar('=', '[', ']'), ' ', progressbar.Percentage()])

                self.vec_env = env

                #class Struct(object): pass
                #self.report = Struct()
                #self.report.history = MaxSizeList(save_stats_freq)
                self.report = self.vec_env.get_attr("history")[0]
                for stat in self.report.keys():
                    self.report[stat] = []

            def _init_callback(self) -> None:
                # Create folder if needed
                if self.save_path is not None:
                    os.makedirs(self.save_path, exist_ok=True)

                #self.report.config = self.training_env.get_attr('config')[0]
                #self.report.nsensors = self.report.config["n_sensors_per_sector"] * self.report.config["n_sectors"]
                #self.report.sensor_angle = 2 * np.pi / (self.report.nsensors + 1)

                self.bar.start()

            def _on_step(self) -> bool:
                # Checking for both 'done' and 'dones' keywords because:
                # Some models use keyword 'done' (e.g.,: SAC, TD3, DQN, DDPG)
                # While some models use keyword 'dones' (e.g.,: A2C, PPO)
                done_array = np.array(
                    self.locals.get("done") if self.locals.get("done") is not None else self.locals.get("dones"))


                if np.sum(done_array).item() > 0:
                    self.n_episodes += np.sum(done_array).item()
                    self.logger.record('time/episodes', self.n_episodes)
                    # Tensorboard logging
                    #self.vec_env.env_method('store_statistics_to_file', path=figure_folder)

                    # Fetch stats from history attribute and log to tensorboard
                    stats = np.array(self.vec_env.get_attr("history"))[done_array]
                    for _env in stats:
                        for stat in _env.keys():
                            #self.logger.record('stats/'+stat, _env[stat])
                            if len(_env[stat]) > 0:
                                self.report[stat].append(_env[stat][-1])

                if self.num_timesteps % self.save_stats_freq == 0:
                    gym_auv.reporting.report(self.report, report_dir=figure_folder)

                # Update the progress bar (n_calls is automatically incremented on each step)
                #self.bar.update(self.num_timesteps)


                #env_histories = self.training_env.get_attr('history')
                #for episode in range(max(map(len, env_histories))):
                #    for env_idx in range(len(env_histories)):
                #        if (episode < len(env_histories[env_idx])):
                #            self.report.history.append(env_histories[env_idx][episode])


                # if self.num_timesteps % self.save_stats_freq == 0 and len(self.report.history) > 1:
                #     self.report.last_episode = self.training_env.get_attr('last_episode')[0]
                #     self.report.obstacles = self.training_env.get_attr('obstacles')[0]
                #     self.report.episode = self.n_episodes
                
                #     gym_auv.reporting.report(self.report, report_dir=figure_folder)


                if self.num_timesteps % self.save_agent_freq == 0:
                    print("Saving agent after", self.num_timesteps, "timesteps")
                    agent_filepath = os.path.join(self.log_dir, str(self.num_timesteps) + '.pkl')
                    self.model.save(agent_filepath)
                #
                #    if args.pilot:
                #        cmd = 'python run.py enjoy {} --agent "{}" --video-dir "{}" --video-name "{}" --recording-length {} --algo {} --pilot {} --envconfig {}{}'.format(
                #            args.env, self.log_dir, video_folder, args.env + '-' + str(self.num_timesteps),
                #            recording_length, args.algo, args.pilot, envconfig_string,
                #            ' --recurrent' if args.recurrent else ''
                #        )
                #if self.num_timesteps % self.record_agent_freq == 0:
                    #agent_filepath = os.path.join(self.log_dir, str(self.num_timesteps) + '.pkl')
                    # cmd = 'python run.py enjoy {} --agent "{}" --video-dir "{}" --video-name "{}" --recording-length {} --algo {} --envconfig {}{}'.format(
                    #     args.env, agent_filepath, video_folder, args.env + '-' + str(self.num_timesteps),
                    #     recording_length, args.algo, envconfig_string,
                    #     ' --recurrent' if args.recurrent else ''
                    # )
                    #cmd = 'python run.py enjoy {} --agent "{}"'.format(
                        #args.env, agent_filepath)
                    #subprocess.Popen(cmd, shell=True)

                return True


        callback = CollectStatisticsCallback(env=vec_env, total_timesteps=total_timesteps, save_stats_freq=save_stats_freq,
                                             record_agent_freq=record_agent_freq, log_dir=agent_folder, verbose=1)

        agent.learn(
            total_timesteps=total_timesteps,
            tb_log_name='log',
            callback=callback, 
        )

    elif (args.mode in ['policyplot', 'vectorfieldplot', 'streamlinesplot']):
        figure_folder = os.path.join(DIR_PATH, 'logs', 'plots', args.env, EXPERIMENT_ID)
        os.makedirs(figure_folder, exist_ok=True)
        agent = PPO.load(args.agent)

        if args.testvals:
            testvals = json.load(open(args.testvals, 'r'))
            valuegrid = list(ParameterGrid(testvals))
            for valuedict in valuegrid:
                customconfig = envconfig.copy()
                customconfig.update(valuedict)
                env = create_env(env_id, envconfig, test_mode=True, pilot=args.pilot)
                valuedict_str = '_'.join((key + '-' + str(val) for key, val in valuedict.items()))

                print('Running {} test for {}...'.format(args.mode, valuedict_str))
                
                if args.mode == 'policyplot':
                    gym_auv.reporting.plot_actions(env, agent, fig_dir=figure_folder, fig_prefix=valuedict_str)
                elif args.mode == 'vectorfieldplot':
                    gym_auv.reporting.plot_vector_field(env, agent, fig_dir=figure_folder, fig_prefix=valuedict_str)
                elif args.mode == 'streamlinesplot':
                    gym_auv.reporting.plot_streamlines(env, agent, fig_dir=figure_folder, fig_prefix=valuedict_str)

        else:
            env = create_env(env_id, envconfig, test_mode=True, pilot=args.pilot)
            print(type(env.config))
            with open(os.path.join(figure_folder, 'config.json'), 'w') as f:
                json.dump(env.config, f)

            if args.mode == 'policyplot':
                gym_auv.reporting.plot_actions(env, agent, fig_dir=figure_folder)
            elif args.mode == 'vectorfieldplot':
                gym_auv.reporting.plot_vector_field(env, agent, fig_dir=figure_folder)
            elif args.mode == 'streamlinesplot':
                gym_auv.reporting.plot_streamlines(env, agent, fig_dir=figure_folder)


        print('Output folder: ', figure_folder)

    elif args.mode == 'test':
        figure_folder = os.path.join(DIR_PATH, 'logs', 'tests', args.env, EXPERIMENT_ID)
        scenario_folder = os.path.join(figure_folder, 'scenarios')
        video_folder = os.path.join(figure_folder, 'videos')
        os.makedirs(figure_folder, exist_ok=True)
        os.makedirs(scenario_folder, exist_ok=True)
        os.makedirs(video_folder, exist_ok=True)

        if not args.onlyplot:
            agent = model.load(args.agent)

        def create_test_env(video_name_prefix, envconfig=envconfig):
            print('Creating test environment: ' + env_id)
            env = create_env(env_id, envconfig, test_mode=True, render_mode=args.render if args.video else None, pilot=args.pilot)
            vec_env = DummyVecEnv([lambda: env])
            #vec_env = VecFrameStack(_vec_env, n_stack=3, channels_order='first')
            if args.video:
                video_length = min(500, args.recording_length)
                recorded_env = VecVideoRecorder(vec_env, video_folder, record_video_trigger=lambda x: (x%video_length) == 0, 
                video_length=video_length, name_prefix=video_name_prefix
            )
            active_env = recorded_env if args.video else vec_env

            if envconfig['safety_filter']:
                #activate safety filter
                env.vessel.activate_safety_filter(env, 0)

            return env, active_env

        failed_tests = []
        def run_test(id, reset=True, report_dir=figure_folder, scenario=None, max_t_steps=None, env=None, active_env=None):
            nonlocal failed_tests

            if env is None or active_env is None:
                env, active_env = create_test_env(video_name_prefix=args.env + '_'  + id)
                if envconfig['safety_filter']:
                    #activate safety filter
                    env.vessel.activate_safety_filter(env, 0)

            if scenario is not None:
                obs = active_env.reset()
                env.load(args.scenario)
                print('Loaded', args.scenario)
            else: 
                if reset:
                    obs = active_env.reset()
                else:
                    obs = env.observe()

            gym_auv.reporting.plot_scenario(env, fig_dir=scenario_folder, fig_postfix=id, show=args.onlyplot)
            if args.onlyplot:
                return
            cumulative_reward = 0
            t_steps = 0
            if max_t_steps is None:
                done = False
            else:
                done = t_steps > max_t_steps

            while not done:
                start_time = time()
                action, _states = agent.predict(obs, deterministic=not args.stochastic)
                obs, reward, done, info = active_env.step(action)
                if args.video:
                    active_env.render()
                t_steps += 1
                cumulative_reward += reward[0]
                report_msg = '{:<20}{:<20}{:<20.2f}{:<20.2%}{:0.1f}fps\r'.format(
                    id, t_steps, cumulative_reward, info[0]['progress'], 1/(time() - start_time))
                sys.stdout.write(report_msg)
                sys.stdout.flush()

                #if max_t_steps:
                #    if t_steps >= max_t_steps:
                #        done = True

                if args.save_snapshots and t_steps % 1000 == 0 and not done:
                    env.save_latest_episode(save_history=False)
                    for size in (20, 50, 100, 200, 300, 400, 500):
                        gym_auv.reporting.plot_trajectory(figure_folder,
                            env, fig_dir=scenario_folder, fig_prefix=(args.env + '_t_step_' + str(t_steps) + '_' + str(size) + '_' + id), local=True, size=size
                        )
                elif done:
                    gym_auv.reporting.plot_trajectory(figure_folder, env, fig_dir=scenario_folder, fig_prefix=(args.env + '_' + id))
                    if info[0]['collision']:
                        failed_tests.append(id)
                        print("\nCOLLISION IN", id)

            env.close()

            if failed_tests:
                with open(os.path.join(figure_folder, 'failures.txt'), 'w') as f:
                    f.write(', '.join(map(str, failed_tests)))

            # Thomas: uncomment after fixing logging to HDF5-files in training
            gym_auv.reporting.report(env.history, report_dir=report_dir, lastn=100)

            # gym_auv.reporting.plot_trajectory(env, fig_dir=scenario_folder, fig_prefix=(args.env + '_' + id))
            # env.save(os.path.join(scenario_folder, id))

            return copy.deepcopy(env.last_episode)

        print('Testing scenario "{}" for {} episodes.\n '.format(args.env, args.episodes))
        report_msg_header = '{:<20}{:<20}{:<20}{:<20}{:<20}{:<20}{:<20}'.format('Episode', 'Timesteps', 'Cum. Reward', 'Progress', 'Collisions', 'CT-Error [m]', 'H-Error [deg]')
        print(report_msg_header)
        print('-'*len(report_msg_header)) 

        if args.testvals:
            testvals = json.load(open(args.testvals, 'r'))
            valuegrid = list(ParameterGrid(testvals))
        if args.scenario:
            if args.testvals:
                episode_dict = {}
                for valuedict in valuegrid:
                    customconfig = envconfig.copy()
                    customconfig.update(valuedict)
                    env, active_env = create_test_env(envconfig=customconfig)
                    valuedict_str = '_'.join((key + '-' + str(val) for key, val in valuedict.items()))

                    colorval = -np.log10(valuedict['reward_lambda']) #should be general
                    
                    rep_subfolder = os.path.join(figure_folder, valuedict_str)
                    os.makedirs(rep_subfolder, exist_ok=True)
                    for episode in range(args.episodes):
                        last_episode = run_test(valuedict_str + '_ep' + str(episode), report_dir=rep_subfolder)
                        episode_dict[valuedict_str] = [last_episode, colorval]
                print('Plotting all')
                gym_auv.reporting.plot_trajectory(figure_folder, env, fig_dir=scenario_folder, fig_prefix=(args.env + '_all_agents'), episode_dict=episode_dict)

            else:
                run_test("ep0", reset=True, scenario=args.scenario, max_t_steps=5000)

        else:
            safety_filter_comparison = False
            agents = ['10000.pkl', '50000.pkl', '100000.pkl', '500000.pkl']
            agent_path = args.agent[:-9]

            if safety_filter_comparison:
                episode_dict = {}
                agent_index = 0

                customconfig = envconfig.copy()
                env, active_env = create_test_env(envconfig=customconfig, video_name_prefix=args.env)
                valuedict_str = "test"

                
                rep_subfolder = os.path.join(figure_folder, valuedict_str)
                os.makedirs(rep_subfolder, exist_ok=True)
                idx = 0


                colors = ['b', 'r', 'orange','purple']
                for episode in range(args.episodes):
                    #if episode % 2 == 0:
                    
                    envconfig['safety_filter'] = True
                    agent = model.load(agent_path + agents[idx])
                    colorval = colors[idx]
                    
                    idx += 1

                    # else:
                    #     envconfig['safety_filter'] = False
                    #     colorval = "orangered"


                    last_episode = run_test(valuedict_str + '_ep' + str(episode), report_dir=rep_subfolder, max_t_steps=10000)
                    episode_dict['Agent ' + str(agent_index)] = [last_episode, colorval]
                    agent_index += 1

                env.last_episode = last_episode


                #find failed indices
                failed_idx = []
                for failed_test in failed_tests:
                    failed_idx.append(int(failed_test[-1]))
                #print('failed_idx', failed_idx)

                          
                gym_auv.reporting.plot_many_trajectories(figure_folder, env, fig_dir=figure_folder, fig_prefix=(args.env + '_all_agents'), episode_dict=episode_dict, failed_idx=failed_idx)
            else:
                env, active_env = create_test_env(video_name_prefix=args.env)
                for episode in range(args.episodes):
                    run_test('ep' + str(episode), env=env, active_env=active_env, max_t_steps=10000)
                print("{:0.2f}% successfull episodes".format(100*(1-len(failed_tests)/args.episodes)))
        if args.video and active_env:
            active_env.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'mode',
        help='Which program mode to run.',
        choices=['play', 'train', 'enjoy', 'test', 'policyplot', 'vectorfieldplot', 'streamlinesplot'],
    )
    parser.add_argument(
        'env',
        help='Name of the gym environment to run.',
        choices=gym_auv.SCENARIOS.keys()
    )
    parser.add_argument(
        '--agent',
        help='Path to the RL agent to simulate.',
    )
    parser.add_argument(
        '--video-dir',
        help='Dir for output video.',
        default='logs/videos/'
    )
    parser.add_argument(
        '--video-name',
        help='Name of output video.',
        default='auto'
    )
    parser.add_argument(
        '--algo',
        help='RL algorithm to use.',
        default='ppo',
        choices=['ppo', 'ddpg', 'td3', 'a2c', 'sac']
    )
    parser.add_argument(
        '--render',
        help='Rendering mode to use.',
        default='2d',
        choices=['2d', '3d', 'both', 'false'] #'both' currently broken
    )
    parser.add_argument(
        '--recording-length',
        help='Timesteps to simulate in enjoy mode.',
        type=int,
        default=2000
    )
    parser.add_argument(
        '--episodes',
        help='Number of episodes to simulate in test mode.',
        type=int,
        default=1
    )
    parser.add_argument(
        '--video',
        help='Record video for test mode.',
        action='store_true'
    )
    parser.add_argument(
        '--onlyplot',
        help='Skip simulations, only plot scenario.',
        action='store_true'
    )
    parser.add_argument(
        '--scenario',
        help='Path to scenario file containing environment data to be loaded.',
    )
    parser.add_argument(
        '--verbose',
        help='Print debugging information.',
        action='store_true'
    )
    parser.add_argument(
        '--envconfig',
        help='Override environment config parameters.',
        nargs='*'
    )
    parser.add_argument(
        '--nomp',
        help='Only use single CPU core for training.',
        action='store_true'
    )
    parser.add_argument(
        '--stochastic',
        help='Use stochastic actions.',
        action='store_true'
    )
    parser.add_argument(
        '--recurrent',
        help='Use RNN for policy network.',
        action='store_true'
    )
    parser.add_argument(
        '--pilot',
        help='If training in a controller environment, this is the pilot agent to control.',
    )
    parser.add_argument(
        '--testvals',
        help='Path to JSON file containing config values to test.',
    )
    parser.add_argument(
        '--save-snapshots',
        help='Save snapshots of the vessel trajectory on a fixed interval.',
    )
    args = parser.parse_args()

    #from win10toast import ToastNotifier
    #toaster = ToastNotifier()
    #try:
    main(args)
    #toaster.show_toast("run.py", "Program is done", duration=10)
    #except Exception as e:
    #    toaster.show_toast("run.py", "Program has crashed", duration=10)
    #    raise e